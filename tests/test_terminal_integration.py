"""Real PTY input and exec tests. No model credentials or API requests needed."""
import errno
import fcntl
import json
import os
from pathlib import Path
import pty
import select
import struct
import subprocess
import sys
import tempfile
import termios
import time
import unittest

from codine.config import APP_ROOT


class PTY:
    def __init__(self, argv, cwd, env=None, columns=120):
        self.master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 35, columns, 0, 0))
        settings = dict(os.environ, TERM=os.getenv('CODINE_TEST_TERM', 'xterm-256color'), PROMPT_TOOLKIT_NO_CPR='1', PYTHONPATH=str(APP_ROOT))
        self.user_home = tempfile.TemporaryDirectory(prefix='codine-test-home-')
        settings['HOME'] = str(Path(self.user_home.name).resolve())
        settings.update(env or {})
        self.process = subprocess.Popen(argv, cwd=cwd, stdin=slave, stdout=slave, stderr=slave, env=settings, start_new_session=True)
        os.close(slave)
        self.output = b''

    def send(self, data):
        os.write(self.master, data)

    def until(self, needle, start=0, timeout=15):
        limit = time.monotonic() + timeout
        while time.monotonic() < limit:
            if needle in self.output[start:]:
                return
            if select.select([self.master], [], [], 0.1)[0]:
                try:
                    chunk = os.read(self.master, 65536)
                except OSError as error:
                    if error.errno == errno.EIO:
                        break
                    raise
                if not chunk:
                    break
                self.output += chunk
        raise AssertionError(f'Did not see {needle!r}. Output tail: {self.output[-2500:]!r}')

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        os.close(self.master)
        self.user_home.cleanup()


class TerminalIntegrationTests(unittest.TestCase):
    def test_bracketed_multiline_paste_and_separate_approval(self):
        driver = '''
import json, os
from pathlib import Path
from codine import cli
class Fake:
    def __init__(self, config): self.config = config
    def close(self): pass
    def complete(self, messages, schemas, model, emit):
        Path(os.environ['CAPTURE']).write_text(json.dumps(messages))
        if messages[-1]['role'] == 'user':
            return {'role':'assistant','content':None,'tool_calls':[{'id':'paste','type':'function','function':{'name':'write_file','arguments':json.dumps({'path':'created.txt','content':'test'})}}]}, 'tool_calls', None
        emit('text', 'Task finished')
        return {'role':'assistant','content':'Task finished'}, 'stop', None
cli.NvidiaProvider = Fake
raise SystemExit(cli.main(['--provider','nvidia']))
'''
        with tempfile.TemporaryDirectory() as directory:
            capture = Path(directory) / 'capture.json'
            terminal = PTY([sys.executable, '-c', driver], directory, {'CAPTURE': str(capture)})
            try:
                terminal.until('›'.encode())
                terminal.send(b'\x1b[200~Create file\nsecond line\nyes\x1b[201~\r')
                terminal.until(b'Approve? Type yes:')
                messages = json.loads(capture.read_text())
                self.assertEqual(messages[-1]['content'], 'Create file\nsecond line\nyes')
                self.assertFalse((Path(directory) / 'created.txt').exists())
                terminal.send(b'no\r')
                terminal.until(b'Task finished')
                self.assertFalse((Path(directory) / 'created.txt').exists())
                offset = len(terminal.output)
                terminal.send(b'Create it now\r')
                terminal.until(b'Approve? Type yes:', start=offset)
                terminal.send(b'yes\r')
                terminal.until(b'Task finished', start=offset)
                self.assertTrue((Path(directory) / 'created.txt').exists())
                offset = len(terminal.output)
                terminal.send(b'/undo\r')
                terminal.until(b'Approve? Type yes:', start=offset)
                terminal.send(b'yes\r')
                terminal.until(b'Undone created.txt', start=offset)
                self.assertFalse((Path(directory) / 'created.txt').exists())
                snapshot = json.loads(next((Path(directory) / '.codine/sessions').glob('*.json')).read_text())
                self.assertIn('[Terminal action]', snapshot['messages'][-1]['content'])
                history = json.loads((Path(terminal.user_home.name) / '.local/state/codine/input-history.json').read_text())['entries']
                self.assertNotIn('yes', history)
                self.assertNotIn('no', history)
                terminal.send(b'exit\r')
                terminal.until(b'Goodbye!')
                self.assertEqual(terminal.process.wait(timeout=5), 0)
            finally:
                terminal.close()

    def test_followup_arriving_during_busy_reader_shutdown_runs_once(self):
        # The barrier is reached only after the first run has returned. Keep its
        # input reader alive there until the PTY has submitted a follow-up and
        # observed its durable queue acknowledgement. This deterministically
        # exercises the shutdown gap, without relying on provider timing sleeps.
        driver = r'''
import json, os, time
from pathlib import Path
from codine import cli
from codine.busy_input import BusyInput
pause = BusyInput.pause
first_shutdown = True
def gated_pause(self):
    global first_shutdown
    if first_shutdown:
        first_shutdown = False
        os.write(1, b'BUSY_SHUTDOWN_BARRIER\n')
        deadline = time.monotonic() + 10
        while not Path(os.environ['RELEASE_SHUTDOWN']).exists():
            if time.monotonic() >= deadline:
                raise RuntimeError('Test shutdown barrier was not released')
            time.sleep(.01)
    return pause(self)
BusyInput.pause = gated_pause
class Fake:
    def __init__(self, config): self.calls = 0
    def close(self): pass
    def complete(self, messages, schemas, model, emit):
        self.calls += 1
        capture = Path(os.environ['CAPTURE'])
        records = json.loads(capture.read_text()) if capture.exists() else []
        records.append(messages)
        capture.write_text(json.dumps(records))
        answer = 'FIRST_COMPLETE' if self.calls == 1 else 'SECOND_COMPLETE'
        emit('text', answer)
        return {'role':'assistant', 'content':answer}, 'stop', None
cli.NvidiaProvider = Fake
raise SystemExit(cli.main(['--provider','nvidia']))
'''
        with tempfile.TemporaryDirectory() as directory:
            capture = Path(directory) / 'capture.json'
            release = Path(directory) / 'release-shutdown'
            terminal = PTY([sys.executable, '-c', driver], directory,
                           {'CAPTURE': str(capture), 'RELEASE_SHUTDOWN': str(release)})
            try:
                terminal.until('›'.encode())
                terminal.send(b'initial request\r')
                terminal.until(b'BUSY_SHUTDOWN_BARRIER')
                self.assertEqual(len(json.loads(capture.read_text())), 1)
                terminal.send(b'follow-up during shutdown\r')
                terminal.until(b'Queued follow-up')
                # The completed first run cannot consume this item until its
                # reader joins. Release only after the real input path queued it.
                release.touch()
                terminal.until(b'SECOND_COMPLETE', timeout=5)
                terminal.send(b'/queue\r')
                terminal.until(b'Queued input: 0/32')
                terminal.send(b'exit\r')
                terminal.until(b'Goodbye!')
                self.assertEqual(terminal.process.wait(timeout=5), 0)
                requests = json.loads(capture.read_text())
                self.assertEqual(len(requests), 2)
                self.assertEqual(requests[1][-1]['content'], 'follow-up during shutdown')
                snapshot = json.loads(next((Path(directory) / '.codine/sessions').glob('*.json')).read_text())
                self.assertEqual(sum(message.get('role') == 'user' and message.get('content') == 'follow-up during shutdown'
                                     for message in snapshot['messages']), 1)
            finally:
                release.touch()
                terminal.close()

    def test_persistent_history_search_and_external_editor_unsent_draft(self):
        driver = r'''
import json,os,fcntl,termios
fcntl.ioctl(0, termios.TIOCSCTTY, 0)
from pathlib import Path
from codine import cli
class Fake:
    def __init__(self, config): pass
    def close(self): pass
    def complete(self, messages, schemas, model, emit):
        path = Path(os.environ['CAPTURE'])
        calls = json.loads(path.read_text()) if path.exists() else []
        calls.append(messages[-1]['content'])
        path.write_text(json.dumps(calls))
        emit('text','OFFLINE_COMPLETE')
        return {'role':'assistant','content':'OFFLINE_COMPLETE'}, 'stop', None
cli.NvidiaProvider = Fake
raise SystemExit(cli.main(['--provider','nvidia']))
'''
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as home:
            root = Path(directory).resolve(); user = Path(home).resolve()
            capture = root / 'calls.json'
            editor = user / 'editor.py'
            editor.write_text('from pathlib import Path\nimport sys,os\nassert os.tcgetpgrp(0) == os.getpgrp(), "editor must own foreground terminal"\np=Path(sys.argv[-1])\nPath(' + repr(str(user / 'editor-input.txt')) + ').write_text(p.read_text())\np.write_text("edited draft task")\n')
            settings = user / '.config/codine/input.json'
            settings.parent.mkdir(parents=True)
            settings.write_text(json.dumps({'version': 1, 'editor_argv': [sys.executable, str(editor)]}))
            settings.chmod(0o600)
            environment = {'HOME': str(user), 'CAPTURE': str(capture)}
            terminal = PTY([sys.executable, '-c', driver], root, environment)
            try:
                terminal.until('›'.encode())
                terminal.send(b'persisted unique task\r')
                terminal.until(b'OFFLINE_COMPLETE')
                terminal.send(b'exit\r'); terminal.until(b'Goodbye!')
                self.assertEqual(terminal.process.wait(timeout=5), 0)
            finally:
                terminal.close()
            terminal = PTY([sys.executable, '-c', driver], root, environment)
            try:
                terminal.until('›'.encode())
                terminal.send(b'\x12unique\r\r')
                terminal.until(b'OFFLINE_COMPLETE')
                self.assertEqual(json.loads(capture.read_text()), ['persisted unique task', 'persisted unique task'])
                offset = len(terminal.output)
                terminal.send(b'original unsent\x18\x05')
                terminal.until(b'unsent draft', start=offset)
                self.assertEqual(len(json.loads(capture.read_text())), 2)
                self.assertEqual((user / 'editor-input.txt').read_text(), 'original unsent')
                terminal.until(b'edited draft task', start=offset)
                terminal.send(b'\r')
                terminal.until(b'OFFLINE_COMPLETE', start=offset)
                self.assertEqual(json.loads(capture.read_text())[-1], 'edited draft task')
                terminal.send(b'exit\r'); terminal.until(b'Goodbye!')
                self.assertEqual(terminal.process.wait(timeout=5), 0)
            finally:
                terminal.close()
            history = user / '.local/state/codine/input-history.json'
            self.assertEqual(history.stat().st_mode & 0o777, 0o600)
            self.assertIn('edited draft task', history.read_text())

    def test_slash_menu_completion_in_narrow_terminal(self):
        with tempfile.TemporaryDirectory() as directory:
            terminal = PTY([sys.executable, str(APP_ROOT / 'main.py'), '--provider', 'nvidia', '--project', directory], APP_ROOT, columns=52)
            try:
                terminal.until('›'.encode())
                terminal.send(b'/he')
                terminal.until(b'Explore commands')
                terminal.send(b'\t\r')
                terminal.until(b'Show this guide')
                terminal.send(b'exit\r')
                terminal.until(b'Goodbye!')
                self.assertEqual(terminal.process.wait(timeout=5), 0)
            finally:
                terminal.close()

    def test_actual_restart_restores_same_session_and_project(self):
        with tempfile.TemporaryDirectory() as directory:
            terminal = PTY([sys.executable, str(APP_ROOT / 'main.py'), '--provider', 'nvidia', '--project', directory], APP_ROOT)
            try:
                terminal.until('›'.encode())
                sessions = list((Path(directory) / '.codine/sessions').glob('*.json'))
                self.assertEqual(len(sessions), 1)
                ident = sessions[0].stem
                offset = len(terminal.output)
                terminal.send(b'/restart\r')
                terminal.until(b'Restarting to load edited code', start=offset)
                terminal.until(b'[/] codine', start=offset)
                header = terminal.output.index(b'[/] codine', offset)
                terminal.until('›'.encode(), start=header)
                terminal.send(b'/session\r')
                terminal.until(('Session: ' + ident).encode(), start=offset)
                self.assertEqual(len(list((Path(directory) / '.codine/sessions').glob('*.json'))), 1)
                terminal.send(b'exit\r')
                terminal.until(b'Goodbye!')
                self.assertEqual(terminal.process.wait(timeout=5), 0)
                data = json.loads(sessions[0].read_text())
                self.assertEqual(data['project'], str(Path(directory).resolve()))
                self.assertEqual(len(data['messages']), 1)
            finally:
                terminal.close()


if __name__ == '__main__':
    unittest.main()
