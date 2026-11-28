import copy
from dataclasses import dataclass
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
from codine.agent import Agent
from codine.budget import Budget,BudgetError
from codine.config import Config,Redactor
from codine.registry import ToolRegistry
from codine.sessions import Session,SessionStore
from codine.supervisor import Supervisor,validate
from codine.workspace import Workspace

@dataclass
class Limits:
    max_output_tokens:int=4096
    max_seconds:int=60

class FakeWorker:
    def __init__(self,ws,cfg,store,*,complete,redactor,cancel_event,limits,parent_link):
        self.store,self.complete,self.cancel=store,complete,cancel_event
        self.session=Session.create(ws.root,cfg.model,'child',provider=cfg.provider)
        self.session.delegation=parent_link;store.save(self.session)
    def run(self,task):return self.complete([{'role':'user','content':task}],[],lambda *args:None)
    def close(self):self.store.close()

class SupervisorTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name).resolve();self.ws=Workspace(self.root,Mock(),Mock())
        self.store=SessionStore(self.root);self.addCleanup(self.store.close)
        self.session=Session.create(self.root,'fake','system',provider='openrouter')
        self.session.billing=Budget.create().state
        self.cfg=Config(self.root,provider='openrouter',api_key='fake-key',model='fake')
        self.provider=Mock();self.provider.complete.return_value=({'role':'assistant','content':'done'},'stop',SimpleNamespace(cost='0.02',prompt_tokens=10,completion_tokens=2,total_tokens=12))
        self.agent=Agent(self.cfg,self.provider,ToolRegistry(),self.store,self.session,Mock(),Redactor())
        self.agent.configure_shared_budget(self.root/'allowance.json',lambda *args:{'limit_remaining':'5'})
        self.agent.save()
        self.supervisor=Supervisor(self.agent,self.ws,worker_factory=FakeWorker)
        self.addCleanup(patch.stopall)
        patch.dict('sys.modules',{'codine.subagents':SimpleNamespace(ReadOnlyWorker=FakeWorker,WorkerLimits=Limits)}).start()
    def test_parent_only_charge_durable_link_and_child_resume_denied(self):
        self.supervisor.run('read only')
        self.assertEqual(self.session.billing['spent'],'0.02')
        self.assertEqual(self.session.usage_details['requests'],1)
        row=self.session.delegation['runs'][0];self.assertEqual(row['status'],'completed')
        child=self.store._read_session(row['child'],repair_pending=False)
        self.assertEqual(child.delegation,{'role':'child','parent':self.session.id,'run':row['id']})
        independent=SessionStore(self.root);self.addCleanup(independent.close)
        with self.assertRaisesRegex(ValueError,'archival'):independent.load(child.id)
        self.assertEqual(self.provider.complete.call_count,1)
    def test_unknown_cost_remains_pending_and_blocks_next_child(self):
        self.provider.complete.return_value=({'role':'assistant','content':'done'},'stop',SimpleNamespace(prompt_tokens=10))
        with self.assertRaises(BudgetError):self.supervisor.run('task')
        self.assertIsNotNone(self.session.billing['pending'])
        with self.assertRaises(BudgetError):self.supervisor.run('again')
        self.assertEqual(self.provider.complete.call_count,1)
    def test_cancel_before_dispatch_sends_nothing(self):
        self.supervisor.cancel_event.set()
        with self.assertRaises(KeyboardInterrupt):self.supervisor.run('task')
        self.provider.complete.assert_not_called();self.assertIsNone(self.session.billing['pending'])
    def test_deadline_callback_blocks_without_releasing_uncertain_charge(self):
        def provider(*args):
            self.supervisor.deadline=0
            args[-1]('text','partial')
        self.provider.complete.side_effect=provider
        with self.assertRaises(KeyboardInterrupt):self.supervisor.run('task')
        self.assertEqual(self.session.delegation['runs'][0]['status'],'timed_out')
        self.assertIsNotNone(self.session.billing['pending'])
    def test_recovery_explicit_and_no_replay(self):
        self.session.delegation={'role':'parent','runs':[{'id':'a'*32,'child':None,'status':'starting','started':1,'deadline':61}]}
        self.agent.save()
        with self.assertRaisesRegex(ValueError,'unresolved'):self.agent.run_turn('new')
        self.supervisor.recover('a'*32)
        self.assertEqual(self.session.delegation['runs'][0]['status'],'cancelled')
        self.provider.complete.assert_not_called()
    def test_schema_rejects_unbounded_deadlines_or_unknown_roles(self):
        for value in ({'role':'anything'},{'role':'parent','runs':[{'id':'a'*32,'child':None,'status':'running','started':1,'deadline':4000}]}):
            with self.assertRaises(ValueError):validate(value)
    def test_two_active_readers_keep_tasks_disjoint_and_serialize_accounting(self):
        barrier=threading.Barrier(2);seen=[];active=0;maximum=0;guard=threading.Lock()
        class ConcurrentReader(FakeWorker):
            def run(self,task):
                barrier.wait(timeout=3)
                return super().run(task)
        self.supervisor.worker_factory=ConcurrentReader
        original=self.provider.complete.return_value
        def provider(messages,schemas,model,emit):
            nonlocal active,maximum
            with guard:active+=1;maximum=max(maximum,active);seen.append(messages[0]['content'])
            if len(seen)==1:self.assertEqual(sum(row['status']=='running' for row in self.session.delegation['runs']),2)
            with guard:active-=1
            return original
        self.provider.complete.side_effect=provider
        results=self.supervisor.run_many(['first isolated task','second isolated task'])
        self.assertEqual(len(results),2);self.assertEqual(maximum,1)
        self.assertEqual(set(seen),{'first isolated task','second isolated task'})
        self.assertEqual(self.session.billing['spent'],'0.04');self.assertEqual(self.session.usage_details['requests'],2)
        self.assertEqual(len({r['child'] for r in self.session.delegation['runs']}),2)
    def test_unknown_first_charge_prevents_queued_sibling_dispatch(self):
        barrier=threading.Barrier(2)
        class ConcurrentReader(FakeWorker):
            def run(self,task):barrier.wait(timeout=3);return super().run(task)
        self.supervisor.worker_factory=ConcurrentReader
        self.provider.complete.return_value=({'role':'assistant','content':'done'},'stop',SimpleNamespace(prompt_tokens=1))
        with self.assertRaises((BudgetError,KeyboardInterrupt)):self.supervisor.run_many(['one','two'])
        self.assertEqual(self.provider.complete.call_count,1)
        self.assertIsNotNone(self.session.billing['pending'])
        self.assertFalse(self.agent._child_supervision_active)
    def test_shared_cancel_reaches_two_active_readers_without_dispatch(self):
        barrier=threading.Barrier(2)
        owner=self.supervisor
        class ConcurrentReader(FakeWorker):
            def run(self,task):
                barrier.wait(timeout=3);owner.cancel()
                return super().run(task)
        self.supervisor.worker_factory=ConcurrentReader
        with self.assertRaises(KeyboardInterrupt):self.supervisor.run_many(['one','two'])
        self.provider.complete.assert_not_called()
        self.assertTrue(all(r['status']=='cancelled' for r in self.session.delegation['runs']))
    def test_parent_turn_and_other_supervisor_recovery_block_during_lease_gap(self):
        self.agent._child_supervision_active=True
        self.assertEqual(self.session.delegation,{})
        with self.assertRaisesRegex(ValueError,'own completion'):self.agent.run_turn('racing parent turn')
        with self.assertRaisesRegex(ValueError,'owns this parent'):Supervisor(self.agent,self.ws).recover('a'*32)
        with self.assertRaisesRegex(ValueError,'active supervised'):Supervisor(self.agent,self.ws).run('other worker')
        self.provider.complete.assert_not_called()
