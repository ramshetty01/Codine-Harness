import contextlib
import io
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import time
import unittest
from unittest.mock import patch, Mock

from codine.command_output import OutputEvents
from codine.config import Redactor
from codine.events import JsonTerminal
from codine.repository_executor import RepositoryExecutor
from codine.sandbox import SandboxError
from codine.staging import snapshot_repository
from codine.terminal import Terminal
from codine.tools import run_command
from codine.workspace import Workspace
from test_terminal_integration import PTY


class CommandStreamingTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.workspace=Workspace(self.root,lambda _:True,lambda _:None)

    def test_host_output_arrives_before_process_finishes_and_is_bounded(self):
        events=[]
        def receive(event):
            if event['text']:
                self.assertFalse((self.root/'finished').exists())
            events.append(event)
        self.workspace.command_output=receive
        result=run_command(self.workspace,['python','-c',
            'import time,pathlib; print("ready",flush=True); time.sleep(.3); pathlib.Path("finished").touch()'])
        self.assertEqual(result['exit_code'],0)
        self.assertIn('ready',''.join(event['text'] for event in events))
        events.clear(); self.workspace.command_output=events.append
        result=run_command(self.workspace,['python','-c','while True: print("x"*8192,flush=True)'])
        self.assertEqual(result['status'],'output_limit')
        self.assertLessEqual(sum(len(event['text'].encode()) for event in events),24000)

    def test_host_cancel_and_timeout_kill_child_process_group(self):
        # A child ignoring TERM would write this marker unless group KILL works.
        source='''import subprocess,sys,time
child=subprocess.Popen([sys.executable,'-c',"import signal,time,pathlib; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(2); pathlib.Path('escaped').touch()"])
print('ready',flush=True)
time.sleep(60)
'''
        for cancel in (True,False):
            with self.subTest(cancel=cancel):
                interrupted=False
                def receive(event):
                    nonlocal interrupted
                    if cancel and event['text'] and not interrupted:
                        interrupted=True
                        raise KeyboardInterrupt()
                self.workspace.command_output=receive
                if cancel:
                    with self.assertRaises(KeyboardInterrupt):
                        run_command(self.workspace,['python','-c',source],timeout=1)
                else:
                    self.assertEqual(run_command(self.workspace,['python','-c',source],timeout=1)['status'],'timeout')
                time.sleep(2.1)
                self.assertFalse((self.root/'escaped').exists())

    def test_host_normal_exit_cleans_background_child_with_closed_pipes(self):
        child="import time,pathlib; time.sleep(1); pathlib.Path('background').touch()"
        source=("import subprocess,sys; subprocess.Popen([sys.executable,'-c'," + repr(child) +
                "],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)")
        result=run_command(self.workspace,['python','-c',source])
        self.assertEqual(result['exit_code'],0)
        time.sleep(1.1)
        self.assertFalse((self.root/'background').exists())

    def test_denied_cleanup_signal_retries_but_never_claims_unconfirmed_success(self):
        from codine.tools import stop_process
        process = Mock(pid=123)
        with patch('codine.tools.os.killpg', side_effect=[None, PermissionError('transient'), ProcessLookupError()]), \
             patch('codine.tools.time.sleep') as sleep:
            stop_process(process)
            sleep.assert_called_once_with(0.05)
        with patch('codine.tools.os.killpg', side_effect=[None] + [PermissionError('denied')] * 11), \
             patch('codine.tools.time.sleep') as sleep:
            with self.assertRaises(PermissionError):
                stop_process(process)
            self.assertEqual(sleep.call_count, 10)

    def test_utf8_and_split_secret_redaction_for_terminal_and_json(self):
        secret='example-private-token'
        for terminal in (Terminal(Redactor([secret]),interactive=False),JsonTerminal(Redactor([secret]))):
            with self.subTest(terminal=type(terminal).__name__), contextlib.redirect_stdout(io.StringIO()) as output:
                events=OutputEvents(lambda event:terminal.emit('command_output',event))
                for stream in ('stdout','stderr'):
                    events.feed(stream,b'example-pri')
                for stream in ('stdout','stderr'):
                    events.feed(stream,b'vate-token \x1b[31m')
                    for byte in 'é'.encode(): events.feed(stream,bytes([byte]))
                events.finish()
                terminal.emit('end_stream','')
            shown=output.getvalue()
            self.assertNotIn(secret,shown)
            self.assertNotIn('example-pri',shown)
            self.assertNotIn('\x1b',shown)
            self.assertIn('[REDACTED]',shown)
            self.assertNotIn('\ufffd',shown)

    def test_real_cli_interrupt_recovers_journal_and_prompt_without_replay(self):
        driver='''
import json
from codine import cli
class Fake:
 def __init__(self,config): pass
 def close(self): pass
 def complete(self,messages,schemas,model,emit):
  args={'argv':['python','-c','import time; print("LIVE_READY",flush=True); time.sleep(60)']}
  return {'role':'assistant','content':None,'tool_calls':[{'id':'command','type':'function','function':{'name':'run_command','arguments':json.dumps(args)}}]},'tool_calls',None
cli.NvidiaProvider=Fake
raise SystemExit(cli.main(['--provider','nvidia']))
'''
        terminal=PTY([sys.executable,'-c',driver],self.root)
        try:
            terminal.until('›'.encode()); terminal.send(b'run a command\r')
            terminal.until(b'Approve? Type yes:'); terminal.send(b'yes\r')
            terminal.until(b'Command output')
            terminal.until(b'LIVE_READY',start=terminal.output.index(b'Command output'))
            offset=len(terminal.output)
            os.kill(terminal.process.pid,signal.SIGINT)
            terminal.until(b'Turn interrupted.',start=offset)
            terminal.until('›'.encode(),start=offset)
            terminal.send(b'/session\r'); terminal.until(b'Session:',start=offset)
            terminal.send(b'exit\r'); terminal.until(b'Goodbye!')
            self.assertEqual(terminal.process.wait(timeout=5),0)
            saved=json.loads(next((self.root/'.codine/sessions').glob('*.json')).read_text())
            self.assertIsNone(saved['pending'])
            tools=[message for message in saved['messages'] if message['role']=='tool']
            self.assertEqual(len(tools),1)
            self.assertIn('unknown',tools[0]['content'].lower())
        finally:
            terminal.close()


@unittest.skipUnless(os.getenv('CODINE_DOCKER_TESTS')=='1','Requires Docker opt-in')
class DockerStreamingTests(unittest.TestCase):
    def test_live_output_cancel_cleans_container_and_descendants(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'task.py').write_text('import subprocess,time\nsubprocess.Popen(["python","-c","import time; time.sleep(60)"])\nprint("READY",flush=True)\ntime.sleep(60)\n')
            executor=RepositoryExecutor(timeout=5)
            names=[]; control=executor._control
            def track(args):
                if args[0]=='create': names.append(args[args.index('--name')+1])
                return control(args)
            interrupted=False
            def receive(event):
                nonlocal interrupted
                if 'READY' in event['text'] and not interrupted:
                    interrupted=True
                    raise KeyboardInterrupt()
            with patch.object(executor,'_control',side_effect=track),self.assertRaises(KeyboardInterrupt):
                executor.execute(snapshot_repository(root,['task.py']),['python','task.py'],on_output=receive)
            self.assertTrue(interrupted)
            with self.assertRaises(SandboxError): control(['inspect',names[0]])

    def test_live_output_precedes_exit_and_runaway_stream_is_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); path=root/'task.py'
            path.write_text('import time\nprint("READY",flush=True)\ntime.sleep(.4)\n')
            executor=RepositoryExecutor(timeout=5,output_limit=2048)
            timings=[]
            result=executor.execute(snapshot_repository(root,['task.py']),['python','task.py'],
                                    on_output=lambda event:timings.append((time.monotonic(),event)))
            ended=time.monotonic()
            first=next(stamp for stamp,event in timings if 'READY' in event['text'])
            self.assertGreater(ended-first,.3)
            self.assertEqual(result['exit_code'],0)
            path.write_text('while True: print("x"*4096,flush=True)')
            events=[]
            result=executor.execute(snapshot_repository(root,['task.py']),['python','task.py'],on_output=events.append)
            self.assertEqual(result['status'],'output_limit')
            self.assertLessEqual(sum(len(event['text'].encode()) for event in events),2048)
