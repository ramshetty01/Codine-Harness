import json
import os
from pathlib import Path
import signal
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from codine.config import Redactor
from codine.extension_process import ExtensionProcess, ExtensionProcessError
from codine.extension_trust import ExtensionTrustStore
from codine.workspace import Workspace


FIXTURE = '''
import os
print('registration output goes to stderr')
def register(api):
    events = []
    api.tool('echo', 'Echo the supplied text.',
             {'type':'object','properties':{'text':{'type':'string'}},'required':['text'],'additionalProperties':False},
             lambda args: {'text':args['text'], 'keys_present': 'OPENROUTER_API_KEY' in os.environ,
                           'helper': __import__('helper').VALUE})
    api.command('hello', 'Return command input.', lambda text: 'hello '+text)
    api.on('turn_end', lambda payload: events.append(payload['id']) or list(events))
    api.panel('status', lambda: 'plain panel')
'''


class ExtensionProcessTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.home = Path(temp.name).resolve()
        self.root = self.home/'project'; self.root.mkdir()
        self.ws = Workspace(self.root, lambda _:True, lambda _:None)
        self.store = ExtensionTrustStore(self.home/'trust')
        self.redactor = Redactor(['sensitive-test-value'])

    def snapshot(self, source=FIXTURE, *, grant=True):
        (self.root/'main.py').write_text(source)
        (self.root/'helper.py').write_text('VALUE = "staged helper"\n')
        (self.root/'manifest.json').write_text(json.dumps({'version':1,'name':'example',
            'entrypoint':'main.py','files':['main.py','helper.py'],
            'capabilities':['tools','commands','events','ui']}))
        snapshot = self.store.review(self.ws, 'manifest.json')
        if grant: self.store.allow(self.ws, snapshot['digest'], lambda _:True)
        return snapshot

    def start(self, source=FIXTURE, **kwargs):
        worker = ExtensionProcess(self.snapshot(source), self.root, self.store, self.redactor, **kwargs)
        self.addCleanup(worker.close)
        return worker

    def test_four_surfaces_order_minimal_env_and_exact_staged_bytes(self):
        snapshot = self.snapshot()
        (self.root/'helper.py').write_text('raise RuntimeError("must not read live file")')
        with patch.dict(os.environ, {'OPENROUTER_API_KEY':'sensitive-test-value', 'PYTHONPATH':'/untrusted'}):
            worker = ExtensionProcess(snapshot, self.root, self.store, self.redactor)
        self.addCleanup(worker.close)
        descriptors = worker.describe()['registrations']
        self.assertEqual({key:len(value) for key,value in descriptors.items()},
                         {'tools':1,'commands':1,'events':1,'ui':1})
        self.assertEqual(worker.invoke('tools','echo',{'text':'ok'}),
                         {'text':'ok','keys_present':False,'helper':'staged helper'})
        self.assertEqual(worker.invoke('commands','hello','there'),'hello there')
        self.assertEqual(worker.invoke('events','turn_end',{'id':1}),[1])
        self.assertEqual(worker.invoke('events','turn_end',{'id':2}),[1,2])
        self.assertEqual(worker.invoke('ui','status'),'plain panel')
        stage = worker.stage
        worker.close(); worker.close()
        self.assertIsNotNone(worker.process.poll())
        self.assertFalse(stage.exists())

    def test_untrusted_code_never_executes_and_revocation_stops_actions(self):
        marker = self.root/'executed'
        source = f'from pathlib import Path\nPath({str(marker)!r}).write_text("bad")\n'+FIXTURE
        snapshot = self.snapshot(source, grant=False)
        with self.assertRaises(ExtensionProcessError):
            ExtensionProcess(snapshot,self.root,self.store,self.redactor)
        self.assertFalse(marker.exists())
        worker = self.start()
        self.store.revoke(worker.snapshot['digest'])
        with self.assertRaisesRegex(ExtensionProcessError,'revoked'):
            worker.invoke('commands','hello','no')
        self.assertTrue(worker.closed)

    def test_handler_error_once_sanitized_and_retired(self):
        marker = self.root/'count'
        source = f'''from pathlib import Path
def register(api):
    def fail(text):
        path=Path({str(marker)!r})
        path.write_text(path.read_text()+"x" if path.exists() else "x")
        raise ValueError("sensitive-test-value \\x1b[2J")
    api.command('fail','Fails once.',fail)
'''
        worker = self.start(source)
        with self.assertRaises(ExtensionProcessError) as caught:
            worker.invoke('commands','fail','')
        self.assertNotIn('sensitive-test-value',str(caught.exception))
        self.assertNotIn('\x1b',str(caught.exception))
        self.assertEqual(marker.read_text(),'x')
        with self.assertRaises(ExtensionProcessError): worker.invoke('commands','fail','')
        self.assertEqual(marker.read_text(),'x')

    def test_registration_timeout_cleans_worker_and_stage(self):
        source = 'import time\ntime.sleep(20)\n'+FIXTURE
        before = set(Path(tempfile.gettempdir()).glob('codine-extension-*'))
        with patch('codine.extension_process.REGISTER_TIMEOUT',.15):
            with self.assertRaisesRegex(ExtensionProcessError,'timed out'): self.start(source)
        self.assertEqual(set(Path(tempfile.gettempdir()).glob('codine-extension-*')),before)

    def test_invoke_timeout_kills_process_group_descendant(self):
        marker = self.root/'child.pid'
        source = f'''import subprocess,sys,time
from pathlib import Path
def register(api):
    def wait(text):
        child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])
        Path({str(marker)!r}).write_text(str(child.pid))
        time.sleep(30)
    api.command('wait','Wait.',wait)
'''
        worker = self.start(source)
        with patch('codine.extension_process.INVOKE_TIMEOUT',.3):
            with self.assertRaisesRegex(ExtensionProcessError,'timed out'):
                worker.invoke('commands','wait','')
        self.assertIsNotNone(worker.process.poll())
        child = int(marker.read_text())
        # Linux may briefly leave an orphan zombie until init reaps it.
        for _ in range(50):
            try:
                os.kill(child,0)
                stat_path=Path(f'/proc/{child}/stat')
                if stat_path.exists() and stat_path.read_text().split()[2]=='Z': break
            except ProcessLookupError: break
            time.sleep(.02)
        else: self.fail('Extension descendant survived process-group cleanup.')

    def test_cancellation_stops_active_invoke(self):
        source = 'import time\ndef register(api):\n    api.command("wait","Wait.",lambda _:time.sleep(30))\n'
        cancelled = threading.Event()
        worker = self.start(source,cancel_event=cancelled)
        timer = threading.Timer(.1,cancelled.set); timer.start()
        try:
            with self.assertRaises(KeyboardInterrupt): worker.invoke('commands','wait','')
        finally: timer.join()
        self.assertTrue(worker.closed)
        self.assertIsNotNone(worker.process.poll())

    def test_stderr_flood_and_unexpected_exit_are_bounded(self):
        for body in ('import sys\nsys.stderr.write("x"*100000)', 'import os\nos._exit(7)'):
            with self.subTest(body=body), self.assertRaises(ExtensionProcessError):
                self.start(body+'\n'+FIXTURE)

    def test_invalid_schema_result_and_requests_fail_without_replay(self):
        worker = self.start()
        with self.assertRaises(ExtensionProcessError): worker.invoke('tools','echo',{'text':1})
        self.assertTrue(worker.closed)
        worker = self.start()
        with self.assertRaisesRegex(ExtensionProcessError,'request exceeds'):
            worker.invoke('commands','hello','x'*70000)
        # Oversized request rejected before any bytes reach the worker.
        self.assertEqual(worker.invoke('commands','hello','ok'),'hello ok')

    def test_malformed_stdout_protocol_retires_worker(self):
        source = 'import os\nos.write(1,b"not-json\\n")\n'+FIXTURE
        with self.assertRaisesRegex(ExtensionProcessError, 'JSON|protocol'):
            self.start(source)

    def test_cancelled_before_start_never_imports_extension(self):
        marker = self.root/'cancelled-import'
        source = f'from pathlib import Path\nPath({str(marker)!r}).touch()\n'+FIXTURE
        cancelled = threading.Event(); cancelled.set()
        with self.assertRaises(KeyboardInterrupt): self.start(source, cancel_event=cancelled)
        self.assertFalse(marker.exists())

    def test_large_unicode_response_is_bounded(self):
        worker = self.start('def register(api):\n    api.panel("huge",lambda:"🥭"*15000)\n')
        with self.assertRaisesRegex(ExtensionProcessError,'protocol limit'):
            worker.invoke('ui','huge')
        self.assertTrue(worker.closed)
