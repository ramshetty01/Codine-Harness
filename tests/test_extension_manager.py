import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from codine.agent import Agent, PersistenceError
from codine.config import Config, Redactor
from codine.extension_manager import ExtensionManager
from codine.extension_trust import ExtensionTrustStore
from codine.registry import ToolRegistry, ToolSpec
from codine.sessions import Session, SessionStore
from codine.workspace import Workspace

SOURCE = '''
def register(api):
    api.tool('echo', 'Echo input.', {'type':'object', 'properties':{'text':{'type':'string'}},
        'required':['text'], 'additionalProperties':False}, lambda args:args)
    api.command('hello', 'Echo.', lambda text:text)
    api.on('turn_end', lambda event:None)
    api.panel('status', lambda:'ready')
'''

class ExtensionManagerTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.home = Path(temp.name).resolve()
        self.root = self.home/'project'; self.root.mkdir()
        self.approve = Mock(return_value=True)
        self.ws = Workspace(self.root, self.approve, lambda _:None)
        self.registry = ToolRegistry(lambda spec:self.ws.mode != 'plan' or spec.effect == 'read')
        self.trust = ExtensionTrustStore(self.home/'trust')
        self.manager = ExtensionManager(self.ws, self.registry, self.trust, Redactor())
        self.addCleanup(self.manager.close)

    def fixture(self, source=SOURCE, grant=True, name='example'):
        (self.root/'plugin.py').write_text(source)
        (self.root/'manifest.json').write_text(json.dumps({'version':1,'name':name,
            'entrypoint':'plugin.py','files':['plugin.py'],'capabilities':['tools','commands','events','ui']}))
        snapshot = self.trust.review(self.ws, 'manifest.json')
        if grant:self.trust.allow(self.ws, snapshot['digest'], lambda _:True)
        return snapshot

    def test_model_tool_approval_and_plan_mode(self):
        self.fixture(); row = self.manager.load('manifest.json')
        name = row['tools'][0]
        call = {'function':{'name':name,'arguments':'{"text":"hello"}'}}
        self.ws.mode = 'auto-edit'; self.approve.return_value=False
        self.assertEqual(json.loads(self.registry.execute(call)), {'status':'declined'})
        self.approve.assert_called_once()
        self.approve.return_value=True
        self.assertEqual(json.loads(self.registry.execute(call)), {'text':'hello'})
        self.ws.mode='plan'
        self.assertEqual(self.registry.schemas(), [])
        self.assertIn('disabled', self.registry.execute(call))
        for action in (lambda:self.manager.load('manifest.json'), lambda:self.manager.invoke_command('example:hello','x'),
                       lambda:self.manager.panel('example:status')):
            with self.assertRaisesRegex(ValueError,'plan mode'):action()
        self.manager.unload('example')

    def test_untrusted_top_level_never_executes_and_revoke_blocks(self):
        source="from pathlib import Path\nPath('ran').write_text('bad')\n"+SOURCE
        snapshot=self.fixture(source, grant=False)
        with self.assertRaises(ValueError):self.manager.load('manifest.json')
        self.assertFalse((self.root/'ran').exists())
        self.trust.allow(self.ws,snapshot['digest'],lambda _:True)
        self.manager.load('manifest.json')
        self.trust.revoke(snapshot['digest'])
        with self.assertRaises(ValueError):self.manager.invoke_command('example:hello','x')
        self.assertEqual(self.manager.list()[0]['status'],'disabled')

    def test_parent_revalidates_descriptors_and_preserves_core_registry(self):
        self.fixture()
        core=ToolSpec('core','Core',{'type':'object'},lambda:None)
        self.registry.register(core)
        for description in ({'version':2,'registrations':{}}, {'version':1,'registrations':{'tools':[]}}):
            with patch('codine.extension_manager.ExtensionProcess') as cls:
                cls.return_value.describe.return_value=description
                with self.assertRaises(ValueError):self.manager.load('manifest.json')
                cls.return_value.close.assert_called_once()
            self.assertEqual(self.registry.tools,{'core':core})
            self.assertEqual(self.manager.list(),[])

    def test_model_argument_names_cannot_override_dispatch_closure(self):
        source=SOURCE.replace("'text':{'type':'string'}", "'_name':{'type':'string'},'_local':{'type':'string'}").replace("'required':['text']", "'required':[]")
        self.fixture(source); row=self.manager.load('manifest.json')
        result=self.registry.execute({'function':{'name':row['tools'][0],
            'arguments':'{"_name":"another", "_local":"other"}'}})
        self.assertEqual(json.loads(result),{'_name':'another','_local':'other'})

    def test_event_failure_disables_worker_without_corrupting_durable_turn(self):
        source=SOURCE.replace("lambda event:None", "lambda event:1/0")
        self.fixture(source); self.manager.load('manifest.json')
        session=Session.create(self.root,'offline','system'); store=SessionStore(self.root)
        self.addCleanup(store.close)
        events=[]
        agent=Agent(Config(self.root),Mock(),self.registry,store,session,
                    lambda kind,data:events.append((kind,data)),Redactor())
        agent.extension_manager=self.manager
        with patch.object(agent,'_run_turn',return_value='completed'):
            self.assertEqual(agent.run_turn('task'),'completed')
        self.assertEqual(store.load(session.id).last_run['outcome'],'completed')
        self.assertEqual(len([e for e in events if e[0]=='run_end']),1)
        self.assertEqual(self.manager.list()[0]['status'],'disabled')
        self.assertTrue(any('disabled' in str(data) for kind,data in events if kind=='status'))

    def test_completion_event_after_save_and_never_on_failed_save_or_pause(self):
        session=Session.create(self.root,'offline','system'); store=SessionStore(self.root)
        self.addCleanup(store.close)
        agent=Agent(Config(self.root),Mock(),self.registry,store,session,lambda *a:None,Redactor())
        manager=Mock(); agent.extension_manager=manager
        def observe(record):
            self.assertEqual(store.load(session.id).last_run['outcome'],'completed')
            return []
        manager.turn_end.side_effect=observe
        with patch.object(agent,'_run_turn',return_value='completed'):agent.run_turn('task')
        manager.turn_end.assert_called_once()
        manager.reset_mock()
        with patch.object(agent,'_run_turn',return_value='step_limit'):agent.run_turn('task')
        manager.turn_end.assert_not_called()
        with patch.object(agent,'_run_turn',return_value='completed'), patch.object(store,'save',side_effect=OSError('full')):
            with self.assertRaises(PersistenceError):agent.run_turn('task')
        manager.turn_end.assert_not_called()
