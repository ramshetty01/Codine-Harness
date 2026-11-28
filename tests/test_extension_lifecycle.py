"""Real worker/runtime lifecycle through explicit terminal controls; no model API."""
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from codine.config import Redactor
from codine.extension_commands import command
from codine.extension_trust import ExtensionTrustStore
from codine.registry import ToolRegistry
from codine.workspace import Workspace


def source(version):
    return '''def register(api):
    api.tool("echo", "Echo test input", {
        "type": "object", "properties": {"text": {"type": "string"}},
        "required": ["text"], "additionalProperties": False
    }, lambda args: {"version": VERSION, "text": args["text"]})
    api.command("hello", "Greet test input", lambda text: {"version": VERSION, "text": text})
    api.panel("status", lambda: "panel " + VERSION)
'''.replace('VERSION', repr(version))


class ExtensionLifecycleTests(unittest.TestCase):
    def setUp(self):
        from codine.extension_manager import ExtensionManager
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.home=Path(temp.name).resolve()
        self.root=self.home/'project';self.root.mkdir()
        self.package=self.root/'demo';self.package.mkdir()
        self.entrypoint=self.package/'main.py';self.entrypoint.write_text(source('v1'))
        self.manifest={'version':1,'name':'demo','entrypoint':'main.py','files':['main.py'],
                       'capabilities':['tools','commands','ui']}
        (self.package/'manifest.json').write_text(json.dumps(self.manifest))
        self.approve=Mock(return_value=True)
        self.ws=Workspace(self.root,self.approve,Mock())
        self.registry=ToolRegistry()
        self.trust=ExtensionTrustStore(self.home/'trust')
        self.manager=ExtensionManager(self.ws,self.registry,self.trust,Redactor(),threading.Event())
        self.addCleanup(self.manager.close)
        self.agent=SimpleNamespace(extension_manager=self.manager,_in_turn=False)
        self.ui=SimpleNamespace(write=Mock())

    def grant(self):
        snapshot=self.trust.review(self.ws,'demo/manifest.json')
        self.assertTrue(self.trust.allow(self.ws,snapshot['digest'],self.approve))
        return snapshot['digest']

    def dispatch(self, text):
        return command('extensions',text,self.agent,self.ws,self.ui)

    def invoke_tool(self):
        name=self.manager.list()[0]['tools'][0]
        return json.loads(self.registry.execute({'function':{'name':name,'arguments':'{"text":"sample"}'}}))

    def test_real_load_failed_reload_retrust_reload_and_unload(self):
        # Discovery doesn't load workers or register tools.
        self.assertEqual(len(self.dispatch('discover')['extensions']),1)
        self.assertEqual(self.manager.list(),[])
        self.assertEqual(self.registry.schemas(),[])
        with self.assertRaises(ValueError):self.dispatch('load demo/manifest.json')
        original_digest=self.grant()
        self.dispatch('load demo/manifest.json')
        self.assertEqual(self.manager.list()[0]['digest'],original_digest)
        original_tool=self.manager.list()[0]['tools'][0]
        self.assertEqual(self.invoke_tool(),{'version':'v1','text':'sample'})
        self.assertEqual(command('ext','demo:hello user text',self.agent,self.ws,self.ui),
                         {'version':'v1','text':'user text'})
        self.assertEqual(self.dispatch('panel demo:status'),'panel v1')
        self.entrypoint.write_text(source('v2'))
        with self.assertRaises(ValueError):self.dispatch('reload demo')
        # The failed replacement doesn't unload a still-trusted original snapshot.
        self.assertEqual(self.manager.list()[0]['digest'],original_digest)
        self.assertIn(original_tool,self.registry.tools)
        self.assertEqual(self.invoke_tool()['version'],'v1')
        replacement_digest=self.grant()
        self.assertNotEqual(original_digest,replacement_digest)
        self.dispatch('reload demo')
        self.assertEqual(self.manager.list()[0]['digest'],replacement_digest)
        self.assertEqual(self.invoke_tool()['version'],'v2')
        self.assertEqual(self.dispatch('panel demo:status'),'panel v2')
        self.dispatch('unload demo')
        self.assertEqual(self.manager.list(),[])
        self.assertNotIn(original_tool,self.registry.tools)
        with self.assertRaises(ValueError):command('ext','demo:hello gone',self.agent,self.ws,self.ui)

    def test_trusted_broken_replacement_preserves_original_registration(self):
        original_digest=self.grant();self.dispatch('load demo/manifest.json')
        self.entrypoint.write_text('def register(api):\n    raise ValueError("broken replacement")\n')
        self.grant()
        with self.assertRaises((ValueError,RuntimeError,OSError)):
            self.dispatch('reload demo')
        self.assertEqual(self.manager.list()[0]['digest'],original_digest)
        self.assertEqual(self.invoke_tool()['version'],'v1')

    def test_busy_controls_and_plan_mode_cannot_load_or_execute(self):
        self.grant()
        self.agent._in_turn=True
        with self.assertRaisesRegex(ValueError,'idle'):self.dispatch('load demo/manifest.json')
        self.assertEqual(self.manager.list(),[])
        self.agent._in_turn=False
        self.ws.mode='plan'
        with self.assertRaises(ValueError):self.dispatch('load demo/manifest.json')
        self.ws.mode='ask';self.dispatch('load demo/manifest.json')
        self.ws.mode='plan'
        with self.assertRaises(ValueError):command('ext','demo:hello blocked',self.agent,self.ws,self.ui)
        with self.assertRaises(ValueError):self.dispatch('panel demo:status')
        result=self.invoke_tool()
        self.assertIn('error',result)

    def test_idle_execution_uses_fresh_cancel_scope_and_restores_finished_turn(self):
        stale=threading.Event();stale.set();self.ws.cancel_event=stale
        self.grant()
        self.dispatch('load demo/manifest.json')
        self.assertIs(self.ws.cancel_event,stale)
        self.assertTrue(stale.is_set())
        self.assertEqual(command('ext','demo:hello after cancel',self.agent,self.ws,self.ui),
                         {'version':'v1','text':'after cancel'})
        self.assertEqual(self.dispatch('panel demo:status'),'panel v1')
        self.dispatch('reload demo')
        self.assertIs(self.ws.cancel_event,stale)
        # Direct model/runtime execution still honors the active turn cancellation.
        with self.assertRaises(KeyboardInterrupt):self.manager.invoke_command('demo:hello','blocked')
        self.assertIs(self.ws.cancel_event,stale)

    def test_idle_scope_restores_cancel_flag_on_failed_load(self):
        stale=threading.Event();stale.set();self.ws.cancel_event=stale
        with self.assertRaises(ValueError):self.dispatch('load demo/manifest.json')
        self.assertIs(self.ws.cancel_event,stale)
        self.assertTrue(stale.is_set())
