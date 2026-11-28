"""Bounded managed-state accounting and reviewed retention into protected trash."""
from contextlib import contextmanager
from dataclasses import asdict
import fcntl
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time
import uuid

from .extension_manifest import canonical, read_bytes
from .input_settings import directory
from .rename_noreplace import rename_noreplace
from .storage_lock import mutation

CATEGORIES = {'sessions':20_000_000,'artifacts':2_000_000,'changes':12_000_000,'changesets':24_000_000}
PATTERNS = {'sessions':r'[A-Za-z0-9][A-Za-z0-9_-]{0,99}', 'artifacts':r'[0-9a-f]{64}',
            'changes':r'[0-9]{19,20}-[0-9a-f]{8}', 'changesets':r'[0-9a-f]{32}'}
MAX_ENTRIES=5000
MAX_READ=64_000_000


def digest(value):return hashlib.sha256(canonical(value)).hexdigest()

def stamp(info):return [info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns,info.st_mode]

def private(info):
    if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_uid!=os.getuid() or info.st_mode & 0o077:
        raise ValueError('Storage records must be private owned regular files with one link.')


class Storage:
    def __init__(self,agent,workspace,clock=time.time):
        self.agent,self.workspace,self.clock=agent,workspace,clock
        self.root=workspace.root
        self.plans={}

    def idle(self):
        from .supervisor import require_resolved
        require_resolved(self.agent.session)
        if (getattr(self.agent, '_child_supervision_active', False) or
                any(row['status']=='running' for row in self.agent.session.worker_tasks.get('tasks',[]))):
            raise ValueError('Storage retention requires released child supervision ownership.')
        if getattr(self.agent,'_in_turn',False) or self.agent.session.pending or self.agent.session.last_run.get('outcome')=='running':
            raise ValueError('Storage retention requires an idle session with no pending tool journal.')
        if getattr(self.agent,'inbox',None) and self.agent.inbox.list():
            raise ValueError('Run or drop queued input before storage retention.')
        jobs=getattr(self.workspace,'jobs',None)
        if jobs and any(row['status'] in ('starting','running','queued') or row.get('cleanup_uncertain') for row in jobs.status()):
            raise ValueError('Finish jobs and resolve cleanup uncertainty before storage retention.')

    @contextmanager
    def sessions_idle(self):
        """EX publication lock first; hold every other existing session lease until done."""
        handles=[]
        try:
            with directory(self.root/'.codine/sessions') as parent:
                count=0
                with os.scandir(parent) as entries:
                    for entry in entries:
                        count+=1
                        if count>MAX_ENTRIES:raise ValueError('Session lock inventory exceeds scan bounds.')
                        if not entry.name.endswith('.lock'):continue
                        ident=entry.name[:-5]
                        if not re.fullmatch(PATTERNS['sessions'],ident):raise ValueError('Unknown session lock entry.')
                        info=entry.stat(follow_symlinks=False)
                        if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_uid!=os.getuid() or info.st_mode & 0o022:
                            raise ValueError('Unsafe session lock.')
                        if ident==self.agent.session.id:
                            owned=getattr(self.agent.store,'lock',None)
                            if owned is None or self.agent.store.active!=ident or os.fstat(owned.fileno()).st_ino!=info.st_ino:
                                raise ValueError('Current session must hold its own lock before retention.')
                            continue
                        fd=os.open(entry.name,os.O_RDWR|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=parent);handles.append(fd)
                        opened=os.fstat(fd)
                        if stamp(opened)!=stamp(info):raise ValueError('Session lock changed during inventory.')
                        try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
                        except BlockingIOError:raise ValueError('Another project session is open; close it before retention.') from None
            yield
        finally:
            for fd in handles:os.close(fd)

    def inventory(self,read=False):
        rows=[];counts={key:{'files':0,'bytes':0} for key in (*CATEGORIES,'retained_trash')}
        total_read=0;scanned=0;problems=[];trash_text=[];deadline=time.monotonic()+5
        def read_raw(path,info,limit):
            nonlocal total_read
            private(info)
            if info.st_size>limit or total_read+info.st_size>MAX_READ:
                raise ValueError('Reference inventory exceeds bounded read allowance.')
            raw,identity=read_bytes(self.root,path,limit);total_read+=len(raw)
            after=(self.root/path).stat(follow_symlinks=False)
            if stamp(info)!=stamp(after) or identity!=[info.st_dev,info.st_ino]:raise ValueError('Storage record changed during scan.')
            return raw
        def read_record(path,info,category):
            raw=read_raw(path,info,CATEGORIES[category])
            try:value=json.loads(raw)
            except (UnicodeError,RecursionError) as error:raise ValueError('Invalid storage record encoding or nesting.') from error
            if not isinstance(value,dict):raise ValueError('Storage record must be an object.')
            return raw,value
        for category in CATEGORIES:
            try:
                with directory(self.root/'.codine'/category) as parent:
                    with os.scandir(parent) as entries:
                        for entry in entries:
                            scanned+=1
                            if scanned>MAX_ENTRIES or time.monotonic()>deadline:raise ValueError('Storage inventory exceeds entry/time bounds.')
                            info=entry.stat(follow_symlinks=False)
                            if not entry.name.endswith('.json') or not re.fullmatch(PATTERNS[category],entry.name[:-5]):
                                if category=='sessions' and entry.name.endswith('.inbox'):
                                    if read:
                                        raw=read_raw('.codine/sessions/'+entry.name,info,128000)
                                        queue=json.loads(raw)
                                        if not isinstance(queue,list):raise ValueError('Malformed stored input queue.')
                                        if queue:problems.append('A stored input queue is nonempty; retention blocked.')
                                    continue
                                if entry.name.endswith('.lock') or entry.name=='.lock':continue
                                problems.append('Unrecognized managed-state entry; retention blocked.');continue
                            counts[category]['files']+=1;counts[category]['bytes']+=info.st_size
                            path=f'.codine/{category}/{entry.name}'
                            row={'category':category,'id':entry.name[:-5],'path':path,'bytes':info.st_size,'stamp':stamp(info)}
                            try:
                                private(info)
                                if read:
                                    raw,value=read_record(path,info,category)
                                    row.update(sha256=hashlib.sha256(raw).hexdigest(),value=value,text=raw.decode('utf-8'))
                            except (OSError,ValueError):problems.append('Unsafe, malformed, changed or oversized managed-state record; retention blocked.')
                            rows.append(row)
            except FileNotFoundError:pass
            except (OSError,ValueError,RecursionError):problems.append('Managed category unavailable or outside scan bounds; retention blocked.')
        # Retained archives are still references. Never prune something a backup may need.
        try:
            with directory(self.root/'.codine/storage-trash') as trash:
                with os.scandir(trash) as batches:
                    for batch in batches:
                        scanned+=1
                        if scanned>MAX_ENTRIES or time.monotonic()>deadline:raise ValueError('Trash scan bound reached.')
                        if not re.fullmatch('[0-9a-f]{32}',batch.name):raise ValueError('Unknown trash entry.')
                        with directory(self.root/'.codine/storage-trash'/batch.name) as folder:
                            with os.scandir(folder) as entries:
                                for entry in entries:
                                    scanned+=1
                                    if scanned>MAX_ENTRIES or time.monotonic()>deadline:raise ValueError('Trash scan bound reached.')
                                    info=entry.stat(follow_symlinks=False);private(info)
                                    counts['retained_trash']['files']+=1;counts['retained_trash']['bytes']+=info.st_size
                                    if not read:continue
                                    if entry.name=='manifest.json':
                                        raw=read_raw('.codine/storage-trash/'+batch.name+'/manifest.json',info,1000000)
                                        manifest=json.loads(raw)
                                        if not isinstance(manifest,dict) or manifest.get('status')!='complete':
                                            raise ValueError('Malformed or pending storage recovery journal.')
                                        continue
                                    category,separator,name=entry.name.partition('--')
                                    if not separator or category not in CATEGORIES:raise ValueError('Unknown retained record.')
                                    raw,value=read_record('.codine/storage-trash/'+batch.name+'/'+entry.name,info,category)
                                    trash_text.append(raw.decode('utf-8'))
                                    if category=='sessions':trash_text.append(value.get('id',''))
        except FileNotFoundError:pass
        except (OSError,ValueError,RecursionError):problems.append('Retained trash references could not be fully scanned; retention blocked.')
        return rows,counts,problems,trash_text

    def inspect(self):
        _,counts,problems,_=self.inventory()
        return {'categories':counts,'total_managed_bytes':sum(row['bytes'] for row in counts.values()),
                'warnings':sorted(set(problems)), 'notice':'Managed categories only, not whole-project disk usage. No deletion. Retained trash still occupies disk. Budget, trust, configuration and package recovery state are excluded.'}

    def _plan(self,days,keep,max_bytes,when):
        rows,counts,problems,trash=self.inventory(read=True)
        if problems:raise ValueError(' '.join(sorted(set(problems))))
        # Full session validation without repair or writes.
        for row in rows:
            if row['category']=='sessions':
                self.agent.store._read_session(row['id'],repair_pending=False,payload=row['value'])
        newest=set()
        for category in CATEGORIES:
            newest.update(row['path'] for row in sorted((r for r in rows if r['category']==category),key=lambda r:r['stamp'][3],reverse=True)[:keep])
        sessions={row['id']:row for row in rows if row['category']=='sessions'}
        candidate_sessions=set()
        for ident,row in sessions.items():
            value=row['value'];billing=value.get('billing',{})
            if (ident!=self.agent.session.id and not value.get('delegation') and not value.get('worker_tasks') and not value.get('pending') and value.get('last_run',{}).get('outcome')!='running'
                    and billing.get('pending') is None and not billing.get('blocked') and not billing.get('shared_request')
                    and row['path'] not in newest and row['stamp'][3]/1e9<=when-days*86400):
                candidate_sessions.add(ident)
        # Preserve all history references, including sessions selected for retained trash.
        texts=[row['text'] for row in rows if row['category']!='artifacts']+trash
        retained_ids=set(sessions)|{text for text in trash if re.fullmatch(PATTERNS['sessions'],text)}
        candidates=[];protected={key:0 for key in CATEGORIES};deadline=time.monotonic()+5
        for row in sorted(rows,key=lambda row:row['stamp'][3]):
            if time.monotonic()>deadline:raise ValueError('Reference analysis exceeded its time bound; no retention plan created.')
            category=row['category'];value=row['value'];eligible=False
            if category=='sessions':eligible=row['id'] in candidate_sessions
            elif category=='artifacts':
                if set(value)!={'tool','content'} or not isinstance(value['tool'],str) or not isinstance(value['content'],str):
                    raise ValueError('Malformed artifact record; retention blocked.')
                if hashlib.sha256(row['text'].encode()).hexdigest()!=row['id']:raise ValueError('Artifact content identity mismatch.')
                eligible=not any(row['id'] in text for text in texts) and not any(row['id'] in other['text'] for other in rows if other['category']=='artifacts' and other['id']!=row['id'])
            elif category=='changes':
                eligible=(value.get('id')==row['id'] and isinstance(value.get('path'),str) and
                          isinstance(value.get('after_hash'),str) and re.fullmatch('[0-9a-f]{64}',value['after_hash']) is not None and
                          ('before' in value or value.get('kind')=='lifecycle' and 'before_b64' in value) and value.get('undone') is True and
                          value.get('session_id') not in retained_ids and
                          (value.get('kind')!='lifecycle' or value.get('phase')=='completed'))
            elif category=='changesets':
                eligible=(value.get('id')==row['id'] and type(value.get('version')) is int and value['version']==1 and
                          isinstance(value.get('entries'),dict) and len(value['entries'])<=32 and
                          all(isinstance(entry,dict) and entry.get('rewind')=='restored' for entry in value['entries'].values()) and
                          value.get('status')=='rewound' and value.get('pending') is None
                          and value.get('session_id') not in retained_ids)
            if category in ('changes','changesets') and eligible:
                eligible=not any(row['id'] in other['text'] for other in rows if other is not row) and not any(row['id'] in text for text in trash)
            eligible=eligible and row['path'] not in newest and row['stamp'][3]/1e9<=when-days*86400
            if eligible:candidates.append(row)
            else:protected[category]+=1
        selected=[];total=0
        for row in candidates:
            if len(selected)>=100:break
            if total+row['bytes']>max_bytes:continue
            selected.append({key:row[key] for key in ('category','id','path','bytes','stamp','sha256')});total+=row['bytes']
        state=self.agent.redactor.data(asdict(self.agent.session))
        fingerprint=digest({'records':[{key:row[key] for key in ('path','stamp','sha256')} for row in sorted(rows,key=lambda r:r['path'])],
                            'trash':trash,'active':state})
        plan={'version':1,'project':str(self.root),'when':when,'days':days,'keep':keep,'max_bytes':max_bytes,
              'selected':selected,'bytes':total,'protected':protected,'categories':counts,'inventory':fingerprint,
              'notice':'Move selected records into retained trash; no permanent disk reclamation. Original locations leave normal session/history lookup. Fork ancestry may show missing parents. Active rollback checkpoints remain protected.'}
        plan['digest']=digest(plan)
        return plan

    def plan(self,days=30,keep=20,max_mb=16):
        if any(type(n)is not int for n in (days,keep,max_mb)) or not 1<=days<=36500 or not 1<=keep<=1000 or not 1<=max_mb<=64:
            raise ValueError('Use age days 1–36500, keep count 1–1000 per category, max MiB 1–64.')
        self.idle();self.agent.save()
        with mutation(self.root,exclusive=True),self.sessions_idle():
            plan=self._plan(days,keep,max_mb*1024*1024,self.clock())
        if len(self.plans)>=8:self.plans.pop(next(iter(self.plans)))
        self.plans[plan['digest']]=copy.deepcopy(plan)
        return plan

    def apply(self,ident,approve,emit):
        if self.workspace.mode=='plan':raise ValueError('Storage cleanup is disabled in plan mode; inspect or plan only.')
        self.idle()
        plan=self.plans.get(ident)
        if plan is None:raise ValueError('Create a storage plan in this process before applying its digest.')
        if not plan['selected']:return {'status':'nothing_selected'}
        emit(json.dumps(plan,indent=2))
        if not approve('Move exactly these managed records to retained trash? No permanent deletion; normal lookup stops finding them.'):
            return {'status':'declined'}
        self.idle()
        with mutation(self.root,exclusive=True),self.sessions_idle():
            fresh=self._plan(plan['days'],plan['keep'],plan['max_bytes'],plan['when'])
            if fresh['digest']!=ident:raise ValueError('Storage inventory or active session changed; create a new plan.')
            batch=uuid.uuid4().hex
            with directory(self.root/'.codine/storage-trash',create=True) as trash:
                os.mkdir(batch,0o700,dir_fd=trash)
            with directory(self.root/'.codine/storage-trash'/batch) as target:
                manifest={'version':1,'plan':ident,'status':'pending','entries':plan['selected']}
                def journal():
                    name='.manifest-'+uuid.uuid4().hex
                    fd=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=target)
                    with os.fdopen(fd,'wb') as handle:handle.write(canonical(manifest));handle.flush();os.fsync(handle.fileno())
                    os.replace(name,'manifest.json',src_dir_fd=target,dst_dir_fd=target);os.fsync(target)
                journal()
                for row in manifest['entries']:
                    with directory(self.root/'.codine'/row['category']) as source:
                        name=Path(row['path']).name
                        info=os.stat(name,dir_fd=source,follow_symlinks=False)
                        raw,_=read_bytes(self.root,row['path'],CATEGORIES[row['category']])
                        if stamp(info)!=row['stamp'] or hashlib.sha256(raw).hexdigest()!=row['sha256']:
                            raise ValueError('A selected record changed; partial cleanup retained in trash manifest.')
                        moved=row['category']+'--'+name
                        rename_noreplace(name,moved,src_dir_fd=source,dst_dir_fd=target)
                        os.fsync(source);os.fsync(target)
                        # Preserve racing bytes; never unlink or overwrite on rollback.
                        after=os.stat(moved,dir_fd=target,follow_symlinks=False)
                        raw,_=read_bytes(self.root,'.codine/storage-trash/'+batch+'/'+moved,CATEGORIES[row['category']])
                        if [after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_mode]!=[info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_mode] or hashlib.sha256(raw).hexdigest()!=row['sha256']:
                            raise ValueError('Record changed during move; bytes retained in trash for recovery.')
                        row['moved']=True;journal()
                manifest['status']='complete';journal()
        self.plans.pop(ident,None)
        return {'status':'retained','batch':batch,'bytes_moved':plan['bytes'],'disk_bytes_reclaimed':0}


def command(argument,agent,workspace,ui):
    storage=getattr(agent,'storage',None)
    if storage is None:storage=agent.storage=Storage(agent,workspace)
    parts=argument.split()
    if not parts or parts==['inspect']:result=storage.inspect()
    elif parts[0]=='plan' and len(parts) in (1,4):
        try:args=[int(value) for value in parts[1:]]
        except ValueError:raise ValueError('Use /storage plan [AGE_DAYS KEEP_COUNT MAX_MIB].') from None
        result=storage.plan(*args)
    elif len(parts)==2 and parts[0]=='apply':result=storage.apply(parts[1],ui.approve,ui.write)
    else:raise ValueError('Usage: /storage inspect | plan [AGE_DAYS KEEP_COUNT MAX_MIB] | apply DIGEST')
    ui.write(json.dumps(result,ensure_ascii=False,indent=2))
    return result
