"""Small user-owned terminal palettes; project content cannot select a theme."""
import json
import os
from pathlib import Path
import stat
import uuid

from prompt_toolkit.styles import Style
from .input_settings import directory, private_read, outside_project

THEME_PATH = Path.home() / '.config/codine/theme.json'
NAMES = ('auto', 'dark', 'light', 'contrast')
PALETTES = {
    'dark': {'background':'#0d1117','text':'#e6edf3','muted':'#b1bac4',
             'accent':'#f2c14e','success':'#7ee787','error':'#ff7b72','code':'#79c0ff'},
    'light': {'background':'#ffffff','text':'#182230','muted':'#475467',
              'accent':'#825400','success':'#176637','error':'#b42318','code':'#005f87'},
}


def style_for(name, default):
    if name not in NAMES:raise ValueError('Theme must be auto, dark, light or contrast.')
    if name=='auto':return default
    if name=='contrast':
        return Style.from_dict({'brand':'bold','prompt':'bold','error':'bold',
            'md.heading':'bold','md.strong':'bold','md.link':'underline',
            'bottom-toolbar':'noreverse','completion-menu.completion.current':'reverse bold',
            'completion-menu.meta.completion.current':'reverse bold'})
    p=PALETTES[name]
    return Style.from_dict({
        '':p['text']+' bg:'+p['background'],
        'brand':'bold','border':p['muted'],'muted':p['muted'],'text':p['text'],
        'accent':p['accent'],'success':p['success'],'error':p['error']+' bold',
        'added':p['success'],'removed':p['error'],'prompt':p['accent']+' bold',
        'md.heading':'bold','md.strong':'bold','md.inline':p['code'],
        'md.code':p['text'],'md.fence':p['muted'],'md.quote':p['muted'],
        'md.link':'underline','md.table':p['code'],
        'bottom-toolbar':'noreverse', 'completion-menu.completion':'noreverse',
        'completion-menu.completion.current':'reverse bold',
        'completion-menu.meta.completion':p['muted']+' noreverse',
        'completion-menu.meta.completion.current':'reverse bold',
    })


def load(project, path=None):
    path=Path(path) if path is not None else THEME_PATH
    outside_project(path,project)
    try:
        with directory(path.parent) as fd:
            value=json.loads(private_read(fd,path.name,1024))
    except FileNotFoundError:return 'auto'
    except (UnicodeError,ValueError,RecursionError):raise ValueError('Invalid private theme preference.') from None
    if (not isinstance(value,dict) or set(value)!={'version','theme'} or
            type(value['version']) is not int or value['version']!=1 or value['theme'] not in NAMES):
        raise ValueError('Theme preference requires version 1 and a supported theme name.')
    return value['theme']


def save(project, name, path=None):
    if name not in NAMES:raise ValueError('Theme must be auto, dark, light or contrast.')
    path=Path(path) if path is not None else THEME_PATH
    load(project,path)  # Reject symlinks, unsafe ownership and invalid existing state.
    with directory(path.parent,create=True) as fd:
        temporary='.theme-'+uuid.uuid4().hex
        handle=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=fd)
        try:
            with os.fdopen(handle,'w',encoding='utf-8') as stream:
                json.dump({'version':1,'theme':name},stream);stream.write('\n')
                stream.flush();os.fsync(stream.fileno())
            # An existing preference must still be a private ordinary file.
            try:
                info=os.stat(path.name,dir_fd=fd,follow_symlinks=False)
                if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_nlink!=1 or info.st_mode & 0o077:
                    raise ValueError('Unsafe theme preference target.')
            except FileNotFoundError:pass
            os.replace(temporary,path.name,src_dir_fd=fd,dst_dir_fd=fd);os.fsync(fd)
        finally:
            try:os.unlink(temporary,dir_fd=fd)
            except FileNotFoundError:pass


def command(argument,project,ui):
    if argument and argument!='list':save(project,argument)
    chosen=load(project)
    if hasattr(ui,'apply_theme'):ui.apply_theme(chosen)
    ui.write('Theme: '+chosen+' · choices: '+', '.join(NAMES)+
        '\nNO_COLOR, noninteractive output and TERM=dumb take precedence. '
        'Auto inherits terminal colors; contrast uses emphasis without a color palette.')
