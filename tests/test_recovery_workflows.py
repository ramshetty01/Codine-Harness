"""Recovery across real tool results and persisted sessions, using scripted transport."""
import json
from pathlib import Path
import tempfile
import unittest
from codine.agent import Agent, SYSTEM
from codine.config import Config, Redactor
from codine.registry import ToolSpec
from codine.sessions import Session, SessionStore, validate_messages
from codine.tools import build_registry
from codine.workspace import Workspace


def call(name, **args):
    return {'role':'assistant','content':None,'tool_calls':[{'id':'action','type':'function','function':{'name':name,'arguments':json.dumps(args)}}]}

DONE={'role':'assistant','content':'Verified the available results.'}


class Script:
    def __init__(self, steps): self.steps=iter(steps)
    def complete(self, messages, schemas, model, emit):
        validate_messages(messages)
        step=next(self.steps)
        if callable(step): step=step()
        return step, 'tool_calls' if step.get('tool_calls') else 'stop', None


class RecoveryWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve()
        self.store=SessionStore(self.root)
        self.addCleanup(self.store.close)
        self.session=Session.create(self.root,'scripted',SYSTEM)
        self.workspace=Workspace(self.root,lambda _:True,lambda _:None)
        self.registry=build_registry(self.workspace,lambda:None)

    def agent(self, steps):
        return Agent(Config(self.root),Script(steps),self.registry,self.store,self.session,lambda *a:None,Redactor())

    def test_stale_edit_rereads_and_preserves_external_change(self):
        path=self.root/'task.py'
        path.write_text('value = 1\n')
        def competing_change():
            path.write_text('value = 1\n# written by another editor\n')
            return call('edit_file',path='task.py',old_text='value = 1',new_text='value = 2')
        agent=self.agent([call('read_file',path='task.py'),competing_change,
            call('read_file',path='task.py'),call('edit_file',path='task.py',old_text='value = 1',new_text='value = 2'),DONE])
        self.assertEqual(agent.run_turn('Change the value'),'completed')
        self.assertEqual(path.read_text(),'value = 2\n# written by another editor\n')
        results=[json.loads(m['content']) for m in self.session.messages if m['role']=='tool']
        self.assertIn('error',results[1])
        validate_messages(self.store.load(self.session.id).messages)

    def test_failed_command_is_recorded_then_corrected(self):
        agent=self.agent([call('run_command',argv=['python','-c','raise SystemExit(2)']),
            call('run_command',argv=['python','-c','print("verified")']),DONE])
        self.assertEqual(agent.run_turn('Verify'),'completed')
        results=[json.loads(m['content']) for m in self.session.messages if m['role']=='tool']
        self.assertEqual([r['exit_code'] for r in results],[2,0])
        self.assertIn('verified',results[1]['stdout'])

    def test_interrupt_after_side_effect_resumes_without_replay(self):
        counter=self.root/'counter.txt'
        def interrupt():
            counter.write_text('one execution')
            raise KeyboardInterrupt()
        self.registry.register(ToolSpec('interrupt','test',{'type':'object'},interrupt,'write'))
        agent=self.agent([call('interrupt')])
        with self.assertRaises(KeyboardInterrupt): agent.run_turn('Do work')
        ident=self.session.id
        self.store.close()
        self.store=SessionStore(self.root)
        self.addCleanup(self.store.close)
        self.session=self.store.load(ident)
        self.assertIn('outcome unknown',self.session.messages[-1]['content'])
        agent=self.agent([call('read_file',path='counter.txt'),DONE])
        self.assertEqual(agent.run_turn('Inspect before continuing'),'completed')
        self.assertEqual(counter.read_text(),'one execution')
        validate_messages(self.store.load(ident).messages)
