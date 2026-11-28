"""Serial read-only child supervision with parent-owned accounting and durable links."""
import copy
import math
import re
import threading
import time
import uuid

from .budget import Budget, BudgetError
from .sessions import SessionStore

ACTIVE={'starting','running','interrupted'}
STATES=ACTIVE|{'completed','failed','cancelled','timed_out'}
ID=re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,99}\Z')


def validate(value):
    if value=={}:return
    if not isinstance(value,dict):raise ValueError('Invalid delegation record.')
    if value.get('role')=='child':
        if set(value)!={'role','parent','run'} or not isinstance(value['parent'],str) or not ID.fullmatch(value['parent']) or not isinstance(value['run'],str) or not re.fullmatch('[a-f0-9]{32}',value['run']):raise ValueError('Invalid child supervision link.')
        return
    if set(value)!={'role','runs'} or value['role']!='parent' or not isinstance(value['runs'],list) or len(value['runs'])>16:raise ValueError('Invalid parent supervision record.')
    seen=set()
    for row in value['runs']:
        if not isinstance(row,dict) or set(row)!={'id','child','status','started','deadline'}:raise ValueError('Invalid supervised run fields.')
        if not isinstance(row['id'],str) or not re.fullmatch('[a-f0-9]{32}',row['id']) or row['id'] in seen or row['status'] not in STATES:raise ValueError('Invalid supervised run identity/status.')
        seen.add(row['id'])
        if row['child'] is not None and (not isinstance(row['child'],str) or not ID.fullmatch(row['child'])):raise ValueError('Invalid supervised child identity.')
        if any(type(row[k]) not in (int,float) or not math.isfinite(row[k]) or row[k]<0 for k in ('started','deadline')) or not 0<row['deadline']-row['started']<=3600:raise ValueError('Invalid supervised deadline.')


def require_resolved(session):
    validate(session.delegation)
    if session.delegation.get('role')=='parent' and any(row['status'] in ACTIVE for row in session.delegation['runs']):
        raise ValueError('A supervised child is active or unresolved. Cancel/finish or explicitly recover it before another parent turn.')


class AccountedCompletion:
    def __init__(self,supervisor,run):
        self.supervisor,self.run=supervisor,run
        self.parent_id=supervisor.agent.session.id
        self.model=supervisor.agent.session.model
        self.max_output_tokens=supervisor.agent.config.request_limits(self.model)[1]
    @property
    def billing_snapshot(self):return copy.deepcopy(self.supervisor.agent.session.billing)
    def __call__(self,messages,schemas,emit):
        owner=self.supervisor;owner.check()
        if owner.agent.session.id!=self.parent_id or owner.agent.session.model!=self.model or self.run['status']!='running' or not self.run['child']:raise ValueError('Supervision identity changed; no child request sent.')
        ledger=Budget(owner.agent.session.billing) if owner.agent.session.provider=='openrouter' else None
        def checked(kind,data):owner.check();emit(kind,data)
        # Exactly one parent-owned local/shared reservation and usage record.
        return owner.agent.complete(messages,schemas,ledger,emit=checked)


class Supervisor:
    def __init__(self,agent,workspace,*,worker_factory=None,cancel_event=None,clock=time.time):
        self.agent,self.workspace=agent,workspace
        self.worker_factory=worker_factory
        self.cancel_event=cancel_event or threading.Event()
        self.clock=clock;self.worker=None;self.running=False;self.expired=False;self.deadline=None
    def check(self):
        if self.deadline is not None and self.clock()>=self.deadline:self.expired=True;self.cancel_event.set()
        parent_cancel=getattr(getattr(self.agent,'inbox',None),'cancel',None)
        if self.cancel_event.is_set() or (parent_cancel is not None and parent_cancel.is_set()):raise KeyboardInterrupt('Supervised child cancelled or deadline reached.')
    def cancel(self):
        self.cancel_event.set()
        # Best effort interrupt of an active socket. No hard process-kill claim.
        if self.running:
            try:self.agent.provider.close()
            except Exception:pass
    def run(self,task,*,limits=None):
        from .session_forks import require_idle,settled_shared_budget
        if self.running:raise ValueError('Only one serial supervised child may run.')
        require_resolved(self.agent.session);require_idle(self.agent,self.workspace)
        if self.agent.session.delegation.get('role')=='child':raise ValueError('Recursive child delegation is disabled.')
        from .subagents import ReadOnlyWorker,WorkerLimits
        limits=limits or WorkerLimits(max_output_tokens=self.agent.config.request_limits(self.agent.session.model)[1])
        if self.agent.config.request_limits(self.agent.session.model)[1]>limits.max_output_tokens:raise ValueError('Parent output cap exceeds the worker cap; configure it before delegation.')
        if self.agent.session.provider=='openrouter':
            if getattr(self.agent,'shared_budget_path',None) is None:raise BudgetError('Supervised OpenRouter workers require the parent shared accounting guard.')
            if not self.agent.session.billing:self.agent.session.billing=Budget.create(self.agent.config.budget_usd,self.agent.config.request_budget_usd).state
        with settled_shared_budget(self.agent):pass
        self.check()
        state=self.agent.session.delegation or {'role':'parent','runs':[]}
        if len(state['runs'])>=16:raise ValueError('This parent reached its 16-child durable history limit.')
        started=self.clock();self.deadline=started+limits.max_seconds
        row={'id':uuid.uuid4().hex,'child':None,'status':'starting','started':started,'deadline':self.deadline}
        state['runs'].append(row);self.agent.session.delegation=state;self.agent.save()
        store=SessionStore(self.workspace.root,self.agent.redactor)
        timer=None;self.running=True
        try:
            self.worker=(self.worker_factory or ReadOnlyWorker)(self.workspace,self.agent.config,store,
                complete=AccountedCompletion(self,row),redactor=self.agent.redactor,cancel_event=self.cancel_event,
                limits=limits,parent_link={'role':'child','parent':self.agent.session.id,'run':row['id']})
            row['child']=self.worker.session.id;row['status']='running';self.agent.save()
            def expired():self.expired=True;self.cancel()
            timer=threading.Timer(limits.max_seconds,expired);timer.daemon=True;timer.start()
            self.check();result=self.worker.run(task);self.check()
            row['status']='completed';self.agent.save();return result
        except BaseException as error:
            row['status']='timed_out' if self.expired else 'cancelled' if isinstance(error,KeyboardInterrupt) else 'failed'
            self.agent.save();raise
        finally:
            if timer:timer.cancel()
            if self.worker:self.worker.close()
            else:store.close()
            self.worker=None;self.running=False;self.deadline=None
    def recover(self,run_id):
        """Explicitly retire a dead-process link; never replay or settle unknown cost."""
        if self.running:raise ValueError('A supervised worker is still running.')
        from .session_forks import settled_shared_budget
        row=next((r for r in self.agent.session.delegation.get('runs',[]) if r['id']==run_id),None)
        if row is None or row['status'] not in ACTIVE:raise ValueError('No unresolved supervised run with this ID.')
        if self.agent.session.billing.get('pending') is not None or self.agent.session.billing.get('blocked'):raise BudgetError('Resolve parent charge uncertainty before recovering child supervision.')
        with settled_shared_budget(self.agent):
            store=SessionStore(self.workspace.root,self.agent.redactor)
            try:
                if row['child']:
                    store.acquire(row['child'])
                    child=store._read_session(row['child'],repair_pending=False)
                    if child.delegation!={'role':'child','parent':self.agent.session.id,'run':row['id']}:raise ValueError('Child supervision identity differs.')
                    if child.pending:raise ValueError('Child has an unresolved action journal.')
                row['status']='cancelled';self.agent.save()
            finally:store.close()
