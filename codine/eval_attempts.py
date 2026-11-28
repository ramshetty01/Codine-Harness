"""One retained identity per invocation; never overwrite an earlier attempt."""
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import time
import uuid


class AttemptReport:
    def __init__(self, requested=None, *, prefix='evaluation', directory='.codine/evals'):
        self.id=uuid.uuid4().hex
        self.path=Path(requested) if requested is not None else Path(directory)/(prefix+'-'+self.id+'.json')
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.envelope={'id':self.id,'started':time.time(),'status':'running'}
        fd=os.open(self.path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'wb') as stream:
            raw=json.dumps({'attempt':self.envelope,'results':[]}).encode();stream.write(raw);stream.flush();os.fsync(stream.fileno())
        self.digest=hashlib.sha256(raw).digest()
    def save(self,report,*,status='running'):
        if status not in ('running','completed','aborted'):raise ValueError('Invalid evaluation attempt status.')
        fd=os.open(self.path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        with os.fdopen(fd,'rb') as stream:
            info=os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_size>20_000_000:raise ValueError('Evaluation report identity changed.')
            old=stream.read(20_000_001)
        if hashlib.sha256(old).digest()!=self.digest:raise ValueError('Evaluation report changed externally; refusing to overwrite evidence.')
        self.envelope['status']=status
        if status!='running':
            self.envelope['finished']=time.time()
            self.envelope['elapsed_seconds']=max(0,self.envelope['finished']-self.envelope['started'])
        report['attempt']=dict(self.envelope)
        raw=(json.dumps(report,indent=2,allow_nan=False)+'\n').encode()
        if len(raw)>20_000_000:raise ValueError('Evaluation report exceeds 20 MB.')
        fd,temporary=tempfile.mkstemp(prefix='.attempt-',dir=self.path.parent)
        try:
            with os.fdopen(fd,'wb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
            current=self.path.stat(follow_symlinks=False)
            if (current.st_dev,current.st_ino,current.st_size,current.st_mtime_ns)!=(info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns):raise ValueError('Evaluation report changed during save.')
            os.replace(temporary,self.path);self.digest=hashlib.sha256(raw).digest()
            directory=os.open(self.path.parent,os.O_RDONLY)
            try:os.fsync(directory)
            finally:os.close(directory)
        finally:
            if os.path.exists(temporary):os.unlink(temporary)
