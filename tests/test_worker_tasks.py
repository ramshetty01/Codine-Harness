import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

from codine.agent import Agent
from codine.config import Config,Redactor
from codine.registry import ToolRegistry
from codine.sessions import Session,SessionStore
from codine.workspace import Workspace
from codine.worker_tasks import command,validate
from codine.worker_resources import acquire,release,locked,integration_guard

class WorkerTaskTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name).resolve()
        (self.root/'a.py').write_text('A=1\n');(self.root/'b.py').write_text('B=2\n')
        self.workspace=Workspace(self.root,Mock(return_value=True),Mock())
        self.store=SessionStore(self.root);self.addCleanup(self.store.close)
        session=Session.create(self.root,'fake','system')
        self.provider=Mock();self.provider.complete.return_value=({'role':'assistant','content':'Unverified analysis.'},'stop',None)
        self.agent=Agent(Config(self.root,model='fake',max_tokens=128),self.provider,ToolRegistry(),self.store,session,Mock(),Redactor())
        self.agent.save()
        self.ui=SimpleNamespace(interactive=False,approve=Mock(return_value=True),write=Mock())
    def plan(self,tasks=None):
        tasks=tasks or [{'id':'a','prompt':'Inspect a.py','depends_on':[],'resources':['a.py']},
                       {'id':'b','prompt':'Review prerequisite observations','depends_on':['a'],'resources':['a.py']}]
        command('plan '+json.dumps(tasks),self.agent,self.workspace,self.ui)
    def test_real_supervisor_dependency_waves_durable_results_and_no_replay(self):
        self.plan();self.assertEqual(self.provider.complete.call_count,0)
        command('run',self.agent,self.workspace,self.ui)
        tasks=self.agent.session.worker_tasks['tasks']
        self.assertEqual([row['status'] for row in tasks],['completed','pending'])
        self.assertFalse(tasks[0]['result']['verified'])
        command('run',self.agent,self.workspace,self.ui)
        self.assertEqual(self.provider.complete.call_count,2)
        self.assertIn('Untrusted prerequisite',self.provider.complete.call_args.args[0][-1]['content'])
        command('run',self.agent,self.workspace,self.ui)
        self.assertEqual(self.provider.complete.call_count,2)
        saved=self.store._read_session(self.agent.session.id)
        self.assertEqual(saved.worker_tasks,self.agent.session.worker_tasks)
        with locked(self.workspace) as (rows,_):self.assertEqual(rows,[])
    def test_validation_rejects_cycles_unknowns_unordered_overlap_and_secrets(self):
        first={'id':'a','prompt':'read','depends_on':[],'resources':['a.py']}
        second={'id':'b','prompt':'read','depends_on':[],'resources':['a.py']}
        for tasks in ([first,second],[{**first,'depends_on':['missing']}],
                      [{**first,'depends_on':['b']},{**second,'depends_on':['a']}],
                      [{**first,'resources':['.env']}], [{**first,'resources':['../outside']}],
                      [first]*9):
            with self.subTest(tasks=tasks),self.assertRaises(ValueError):self.plan(tasks)
        self.provider.complete.assert_not_called()
    def test_failed_child_blocks_descendant_and_never_retries(self):
        self.plan()
        self.provider.complete.side_effect=RuntimeError('fake failure')
        with self.assertRaises(RuntimeError):command('run',self.agent,self.workspace,self.ui)
        self.assertEqual([r['status'] for r in self.agent.session.worker_tasks['tasks']],['failed','blocked'])
        command('run',self.agent,self.workspace,self.ui)
        self.provider.complete.assert_called_once()
        with locked(self.workspace) as (rows,_):self.assertEqual(rows,[])
    def test_paused_child_is_not_successful_dependency(self):
        self.plan()
        self.provider.complete.return_value=({'role':'assistant','content':'incomplete'},'length',None)
        command('run',self.agent,self.workspace,self.ui)
        self.assertEqual([r['status'] for r in self.agent.session.worker_tasks['tasks']],['failed','blocked'])
        self.assertEqual(self.agent.session.delegation['runs'][0]['status'],'failed')
    def test_conflicting_owner_blocks_dispatch_and_integration(self):
        self.plan()
        tokens=acquire(self.workspace,'another-parent',[{'id':'other','resources':['a.py']}])
        with self.assertRaisesRegex(ValueError,'conflict'):command('run',self.agent,self.workspace,self.ui)
        with self.assertRaisesRegex(ValueError,'conflicts'):
            with integration_guard(self.workspace,['a.py']):pass
        self.provider.complete.assert_not_called()
        release(self.workspace,'another-parent',tokens)
        with integration_guard(self.workspace,['a.py']):
            with self.assertRaisesRegex(ValueError,'busy'):acquire(self.workspace,'other',[{'id':'x','resources':['a.py']}])
    def test_explicit_recovery_releases_only_owner_and_blocks_descendants(self):
        self.plan();self.agent.session.worker_tasks['tasks'][0]['status']='running';self.agent.save()
        acquire(self.workspace,self.agent.session.id,[{'id':'a','resources':['a.py']}])
        acquire(self.workspace,'other',[{'id':'b','resources':['b.py']}])
        with self.assertRaisesRegex(ValueError,'Recover'):command('run',self.agent,self.workspace,self.ui)
        command('recover',self.agent,self.workspace,self.ui)
        self.assertEqual([r['status'] for r in self.agent.session.worker_tasks['tasks']],['interrupted','blocked'])
        with locked(self.workspace) as (rows,_):self.assertEqual([r['owner'] for r in rows],['other'])
        self.provider.complete.assert_not_called()
    def test_review_mutation_fails_before_dispatch(self):
        self.plan()
        self.ui.approve.side_effect=lambda _:setattr(self.agent.session,'title','changed') or True
        with self.assertRaisesRegex(ValueError,'changed'):command('run',self.agent,self.workspace,self.ui)
        self.provider.complete.assert_not_called()

    def test_reviewed_integration_respects_real_worker_lease(self):
        from codine.isolated_worktrees import IsolatedTrees
        manager=IsolatedTrees(self.workspace,self.root/'trees')
        tree=manager.allocate(['a.py'])
        (Path(tree['path'])/'a.py').write_text('A=2\n')
        review=manager.review(tree['id'])
        tokens=acquire(self.workspace,'other',[{'id':'a','resources':['a.py']}])
        with self.assertRaisesRegex(ValueError,'conflicts'):manager.integrate(review['review'])
        self.assertEqual((self.root/'a.py').read_text(),'A=1\n')
        release(self.workspace,'other',tokens)
        self.assertEqual(manager.integrate(review['review'])['status'],'applied')
        self.assertEqual((self.root/'a.py').read_text(),'A=2\n')

    def test_limits_visible_persisted_and_invalid_limits_do_not_dispatch(self):
        self.plan()
        with self.assertRaises(ValueError):command('run {"max_seconds":121}',self.agent,self.workspace,self.ui)
        self.provider.complete.assert_not_called()
        command('run {"max_steps":2,"max_seconds":30,"max_total_tokens":10000}',self.agent,self.workspace,self.ui)
        self.assertEqual(self.agent.session.worker_tasks['limits']['max_seconds'],30)
        self.assertEqual(self.store._read_session(self.agent.session.id).worker_tasks['limits']['max_steps'],2)
        self.assertIn('max_total_tokens',str(self.ui.write.call_args_list))

    def test_real_rpc_commands_dispatch_then_resume_without_replay(self):
        import sys
        from codine.sdk import Client
        from tests.test_sdk_rpc import BOOTSTRAP
        events=[]
        with Client(self.root,command=[sys.executable,'-u','-c',BOOTSTRAP],approve=lambda _:True,on_event=events.append) as client:
            ident=client.ready['session']
            client.command('/workers plan '+json.dumps([{'id':'a','prompt':'Inspect a.py','depends_on':[],'resources':['a.py']}]))
            client.command('/workers run {"max_steps":2}')
            client.command('/workers status')
        data=json.loads((self.root/'.codine/sessions'/f'{ident}.json').read_text())
        self.assertEqual(data['worker_tasks']['tasks'][0]['status'],'completed')
        self.assertEqual(len(data['delegation']['runs']),1)
        with Client(self.root,command=[sys.executable,'-u','-c',BOOTSTRAP],resume=ident,approve=lambda _:True) as client:
            client.command('/workers run')
        data=json.loads((self.root/'.codine/sessions'/f'{ident}.json').read_text())
        self.assertEqual(len(data['delegation']['runs']),1)

    def test_separate_process_cannot_acquire_owned_resource(self):
        import subprocess,sys
        acquire(self.workspace,self.agent.session.id,[{'id':'a','resources':['a.py']}])
        script='''
from pathlib import Path
from codine.workspace import Workspace
from codine.worker_resources import acquire
import sys
workspace=Workspace(Path(sys.argv[1]),lambda _:False,lambda _:None)
try:acquire(workspace,'second-process',[{'id':'a','resources':['a.py']}])
except ValueError as error:
    assert 'conflict' in str(error)
else:raise AssertionError('Conflicting acquisition succeeded')
'''
        result=subprocess.run([sys.executable,'-c',script,str(self.root)],capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        with locked(self.workspace) as (rows,_):self.assertEqual([r['owner'] for r in rows],[self.agent.session.id])

    def test_cancelled_wave_blocks_dependents_and_releases_ownership(self):
        self.plan()
        self.provider.complete.side_effect=KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):command('run',self.agent,self.workspace,self.ui)
        self.assertEqual([row['status'] for row in self.agent.session.worker_tasks['tasks']],['cancelled','blocked'])
        with locked(self.workspace) as (rows,_):self.assertEqual(rows,[])
        count=self.provider.complete.call_count
        command('run',self.agent,self.workspace,self.ui)
        self.assertEqual(self.provider.complete.call_count,count)
