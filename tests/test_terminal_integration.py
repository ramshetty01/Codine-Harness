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
    def __init__(self, argv, cwd, env=None):
        self.master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 35, 120, 0, 0))
        settings = dict(os.environ, TERM='xterm-256color', PROMPT_TOOLKIT_NO_CPR='1', PYTHONPATH=str(APP_ROOT))
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
raise SystemExit(cli.main())
'''
        with tempfile.TemporaryDirectory() as directory:
            capture = Path(directory) / 'capture.json'
            terminal = PTY([sys.executable, '-c', driver], directory, {'CAPTURE': str(capture)})
            try:
                terminal.until('You ›'.encode())
                terminal.send(b'\x1b[200~Create file\nsecond line\nyes\x1b[201~\r')
                terminal.until(b'Approve? Type yes:')
                messages = json.loads(capture.read_text())
                self.assertEqual(messages[-1]['content'], 'Create file\nsecond line\nyes')
                self.assertFalse((Path(directory) / 'created.txt').exists())
                terminal.send(b'no\r')
                terminal.until(b'Task finished')
                self.assertFalse((Path(directory) / 'created.txt').exists())
                terminal.send(b'exit\r')
                terminal.until(b'Goodbye!')
                self.assertEqual(terminal.process.wait(timeout=5), 0)
            finally:
                terminal.close()

    def test_actual_restart_restores_same_session_and_project(self):
        with tempfile.TemporaryDirectory() as directory:
            terminal = PTY([sys.executable, str(APP_ROOT / 'main.py'), '--project', directory], APP_ROOT)
            try:
                terminal.until('You ›'.encode())
                sessions = list((Path(directory) / '.codine/sessions').glob('*.json'))
                self.assertEqual(len(sessions), 1)
                ident = sessions[0].stem
                offset = len(terminal.output)
                terminal.send(b'/restart\r')
                terminal.until(b'Restarting to load edited code', start=offset)
                terminal.until(('Session: ' + ident).encode(), start=offset)
                terminal.until('You ›'.encode(), start=terminal.output.index(('Session: ' + ident).encode(), offset))
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
