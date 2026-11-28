"""Explicit user-owned local MCP definitions and exact launch reviews."""
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from .extension_trust import ExtensionTrustStore
from .input_settings import directory, private_read
from .prompt_templates import parse_json

MCP_ROOT = Path.home()/'.config/codine/mcp'
PROVIDER_KEYS = {'OPENAI_API_KEY','OPENROUTER_API_KEY','NVIDIA_API_KEY','GROQ_API_KEY','ANTHROPIC_API_KEY'}
WARNING = ('Local MCP servers execute with host-user privileges, not an OS sandbox. '
           'Launch hashes do not attest undeclared imported dependencies. Server tools may access '
           'files, network and paid services independently; Codine does not budget those services.')


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def fingerprint(path, *, capture=False):
    path=Path(path).resolve(strict=True)
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        before=os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size>64*1024*1024:
            raise ValueError('MCP launch files must be regular files under 64 MiB.')
        h=hashlib.sha256(); size=0; chunks=[]
        while True:
            chunk=os.read(fd,65536)
            if not chunk: break
            size+=len(chunk)
            if size>64*1024*1024: raise ValueError('MCP launch file exceeds hash limit.')
            h.update(chunk)
            if capture: chunks.append(chunk)
        after=os.fstat(fd)
        version=lambda v:(v.st_dev,v.st_ino,v.st_size,v.st_mtime_ns,v.st_ctime_ns,v.st_mode)
        if version(before)!=version(after): raise ValueError('MCP launch file changed during review.')
        metadata={'path':str(path),'identity':[before.st_dev,before.st_ino],'sha256':h.hexdigest()}
        return (metadata,b''.join(chunks)) if capture else metadata
    finally: os.close(fd)


class MCPSettings:
    def __init__(self, root=None):
        self.root=Path(root) if root is not None else MCP_ROOT
        self.receipts=ExtensionTrustStore(self.root/'trust')

    def definitions(self):
        try:
            with directory(self.root) as fd:
                raw=parse_json(private_read(fd,'servers.json',32768).decode())
        except FileNotFoundError: return {}
        if (not isinstance(raw,dict) or set(raw)!={'version','servers'} or type(raw['version']) is not int or
                raw['version']!=1 or not isinstance(raw['servers'],dict) or len(raw['servers'])>8):
            raise ValueError('MCP settings require version 1 and at most eight servers.')
        for name,row in raw['servers'].items():
            if not re.fullmatch(r'[a-z][a-z0-9_-]{0,31}',name) or not isinstance(row,dict) or set(row)-{'transport','argv','cwd','files','credential_env','protocol'} or not {'transport','argv','cwd','files','credential_env'}<=set(row):
                raise ValueError('Invalid MCP server definition.')
            if row.get('protocol','auto') not in ('auto','2026-07-28','2025-11-25'):
                raise ValueError('MCP protocol must be auto, 2026-07-28 or 2025-11-25.')
            argv=row['argv']; files=row['files']; env=row['credential_env']
            if row['transport']!='stdio': raise ValueError('Only local stdio MCP servers are supported.')
            if (not isinstance(argv,list) or not 1<=len(argv)<=32 or
                    any(not isinstance(v,str) or not v or len(v)>4096 or '\0' in v for v in argv) or
                    not Path(argv[0]).is_absolute()): raise ValueError('MCP argv needs an absolute executable and bounded literal arguments.')
            if not isinstance(row['cwd'],str) or not Path(row['cwd']).is_absolute(): raise ValueError('MCP cwd must be absolute.')
            if (not isinstance(files,list) or len(files)>16 or
                    any(not isinstance(v,str) or not Path(v).is_absolute() for v in files)):
                raise ValueError('Declare at most sixteen absolute launch script/resource files.')
            if (not isinstance(env,list) or len(env)>16 or any(not isinstance(v,str) or
                    not re.fullmatch('[A-Z][A-Z0-9_]{0,79}',v) or not re.search('TOKEN|KEY|SECRET|PASSWORD|CREDENTIAL',v) or v in PROVIDER_KEYS or
                    v in {'PATH','HOME','PYTHONPATH','PYTHONHOME','LD_PRELOAD','DYLD_INSERT_LIBRARIES'} for v in env)):
                raise ValueError('MCP credential names cannot inherit provider keys or runtime configuration.')
        return raw['servers']

    def snapshot(self,name,project):
        rows=self.definitions()
        if name not in rows: raise ValueError('Unknown user-configured MCP server.')
        config=rows[name]
        root=Path(project).resolve(); info=root.stat()
        cwd=Path(config['cwd']).resolve(strict=True)
        if not cwd.is_dir(): raise ValueError('MCP cwd must be a directory.')
        launch=[fingerprint(config['argv'][0])]+[fingerprint(path) for path in config['files']]
        if not os.access(launch[0]['path'],os.X_OK): raise ValueError('MCP executable is not executable.')
        value={'name':name,'project':str(root),'project_identity':[info.st_dev,info.st_ino],
               'config':config,'cwd':str(cwd),'launch':launch}
        value['digest']=digest(value)
        return value

    def trusted(self,snapshot):
        with self.receipts._locked() as fd:
            return self.receipts._read(fd).get(snapshot['digest'])==snapshot

    def approve(self,name,project,ui,redactor=None):
        before=self.snapshot(name,project)
        from .config import Redactor
        from .transcripts import scrub
        redactor=redactor or Redactor.environment()
        if scrub(json.dumps(before['config']),redactor)!=json.dumps(before['config']):
            raise ValueError('Do not store credential values in MCP config; use credential_env names.')
        ui.write(WARNING+'\n'+json.dumps(before,indent=2))
        if not ui.interactive or not ui.approve('Trust this exact MCP launch definition and declared files?'):
            return False
        after=self.snapshot(name,project)
        if after!=before: raise ValueError('MCP launch changed during approval; review again.')
        with self.receipts._locked() as fd:
            rows=self.receipts._read(fd); rows[before['digest']]=before
            self.receipts._write(fd,rows)
        return True

    def revoke(self,name,project):
        with self.receipts._locked() as fd:
            rows=self.receipts._read(fd)
            rows={k:v for k,v in rows.items() if not (v.get('name')==name and v.get('project')==str(Path(project).resolve()))}
            self.receipts._write(fd,rows)
