"""Private project-wide cooperative resource leases; no filesystem permissions granted."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import PurePosixPath
import re
import stat
import unicodedata
import uuid

from .input_settings import directory, private_read

LIMIT=65536

def strict_pairs(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise ValueError('Duplicate JSON field.')
        result[key]=value
    return result

def load_json(text):
    def invalid(_):raise ValueError('Non-finite JSON value.')
    return json.loads(text,object_pairs_hook=strict_pairs,parse_constant=invalid)

def path_key(path):
    if not isinstance(path,str) or not path or len(path.encode())>1024 or '\\' in path or '\0' in path:
        raise ValueError('Resources require bounded project-relative paths.')
    parsed=PurePosixPath(path)
    if parsed.is_absolute() or str(parsed)!=path or '..' in parsed.parts:
        raise ValueError('Resources must use canonical project-relative paths.')
    return unicodedata.normalize('NFC',path).casefold()

def overlaps(left,right):
    a,b=path_key(left),path_key(right)
    return a=='.' or b=='.' or a==b or a.startswith(b+'/') or b.startswith(a+'/')

def validate(rows):
    if not isinstance(rows,list) or len(rows)>64:raise ValueError('Invalid bounded worker resource inventory.')
    tokens=set()
    for row in rows:
        if not isinstance(row,dict) or set(row)!={'token','owner','task','paths'}:raise ValueError('Invalid resource lease.')
        if not isinstance(row['token'],str) or not re.fullmatch('[a-f0-9]{32}',row['token']) or row['token'] in tokens:raise ValueError('Invalid resource token.')
        tokens.add(row['token'])
        if any(not isinstance(row[k],str) or not re.fullmatch('[A-Za-z0-9_-]{1,100}',row[k]) for k in ('owner','task')):raise ValueError('Invalid resource owner.')
        if not isinstance(row['paths'],list) or not 1<=len(row['paths'])<=8:raise ValueError('Resources require 1–8 paths.')
        for path in row['paths']:path_key(path)
    if len(json.dumps(rows).encode())>LIMIT:raise ValueError('Resource inventory exceeds byte limit.')

@contextmanager
def locked(workspace):
    with directory(workspace.root/'.codine/worker-resources',create=True) as fd:
        handle=os.open('lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW|os.O_NONBLOCK,0o600,dir_fd=fd)
        try:
            info=os.fstat(handle)
            if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_nlink!=1 or info.st_mode&0o077:raise ValueError('Unsafe resource lock.')
            try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise ValueError('Resource ownership is busy; retry later.') from None
            try:rows=load_json(private_read(fd,'leases.json',LIMIT))
            except FileNotFoundError:rows=[]
            validate(rows)
            def save(updated):
                validate(updated)
                name='pending-'+uuid.uuid4().hex
                target=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=fd)
                try:
                    with os.fdopen(target,'w') as stream:
                        json.dump(updated,stream);stream.flush();os.fsync(stream.fileno())
                    os.replace(name,'leases.json',src_dir_fd=fd,dst_dir_fd=fd);os.fsync(fd)
                finally:
                    try:os.unlink(name,dir_fd=fd)
                    except FileNotFoundError:pass
            yield rows,save
        finally:os.close(handle)

def acquire(workspace,owner,tasks):
    with locked(workspace) as (rows,save):
        additions=[]
        for task in tasks:
            for path in task['resources']:
                workspace.resolve(path)
                if any(overlaps(path,other) for row in rows+additions for other in row['paths']):
                    raise ValueError('Resource conflict with an active or unrecovered worker lease.')
            additions.append({'token':uuid.uuid4().hex,'owner':owner,'task':task['id'],'paths':task['resources']})
        save(rows+additions)
        return [row['token'] for row in additions]

def release(workspace,owner,tokens=None):
    with locked(workspace) as (rows,save):
        save([row for row in rows if not (row['owner']==owner and (tokens is None or row['token'] in tokens))])

@contextmanager
def integration_guard(workspace,paths):
    """P3 holds this across approval/CAS/apply; no worker can acquire meanwhile."""
    with locked(workspace) as (rows,_):
        if any(overlaps(path,other) for path in paths for row in rows for other in row['paths']):
            raise ValueError('Reviewed integration conflicts with an active or unrecovered worker resource lease.')
        yield
