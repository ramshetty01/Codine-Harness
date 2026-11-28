"""Real terminal reader ownership, busy submission, approval handoff and stop."""
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from test_terminal_integration import PTY
from codine.config import APP_ROOT
from codine.sessions import validate_messages


class SteeringTerminalTests(unittest.TestCase):
    def test_busy_steering_followup_and_single_approval_reader(self):
        driver = '''
import json, time
from pathlib import Path
from codine import cli
class Fake:
    def __init__(self, config): self.calls=0; self.root=config.project
    def close(self): pass
    def complete(self,messages,schemas,model,emit):
        self.calls+=1
        (self.root / ('request'+str(self.calls)+'.json')).write_text(json.dumps(messages))
        if self.calls==1:
            while not (self.root/'release').exists(): time.sleep(.02)
        if self.calls in (1,3):
            return {'role':'assistant','content':None,'tool_calls':[{'id':'write'+str(self.calls),'type':'function','function':{'name':'write_file','arguments':json.dumps({'path':'created.txt','content':'approved followup'})}}]},'tool_calls',None
        emit('text','FINISHED '+str(self.calls))
        return {'role':'assistant','content':'done'},'stop',None
cli.NvidiaProvider=Fake
raise SystemExit(cli.main(['--provider','nvidia','--project','.']))
'''
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            terminal = PTY([sys.executable, '-c', driver], root)
            try:
                terminal.until('›'.encode())
                terminal.send(b'initial task\r')
                terminal.until('busy ›'.encode())
                deadline = time.monotonic() + 5
                while not (root/'request1.json').exists() and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertTrue((root/'request1.json').exists())
                terminal.send(b'/steer explain only\r')
                terminal.until(b'Queued steering')
                terminal.send(b'write later\r')
                terminal.until(b'Queued follow-up')
                (root/'release').write_text('go')
                terminal.until(b'Approve? Type yes:')
                self.assertFalse((root/'created.txt').exists())
                terminal.send(b'yes\r')
                terminal.until(b'FINISHED 4')
                terminal.send(b'exit\r')
                terminal.until(b'Goodbye!')
                self.assertEqual(terminal.process.wait(timeout=5), 0)
                self.assertEqual((root/'created.txt').read_text(), 'approved followup')
                second = json.loads((root/'request2.json').read_text())
                self.assertEqual(second[-1]['content'], 'explain only')
                self.assertNotIn('write later', json.dumps(second))
                third = json.loads((root/'request3.json').read_text())
                self.assertEqual(third[-1]['content'], 'write later')
                session = json.loads(next((root/'.codine/sessions').glob('*.json')).read_text())
                validate_messages(session['messages'])
                self.assertEqual(len(session['queue_receipts']), 2)
                self.assertEqual(json.loads(next((root/'.codine/sessions').glob('*.inbox')).read_text()), [])
            finally:
                terminal.close()

    def test_stop_foreground_command_keeps_followup_and_does_not_run_next_action(self):
        driver = '''
import json
from codine import cli
class Fake:
    def __init__(self, config): self.calls=0
    def close(self): pass
    def complete(self,messages,schemas,model,emit):
        self.calls+=1
        assert self.calls==1, 'Cancellation must not start followup or retry'
        args={'argv':['python','-c','import time; print("COMMAND_READY",flush=True); time.sleep(30)'],'timeout':40}
        calls=[{'id':'sleep','type':'function','function':{'name':'run_command','arguments':json.dumps(args)}}, {'id':'write','type':'function','function':{'name':'write_file','arguments':json.dumps({'path':'must-not-exist.txt','content':'bad'})}}]
        return {'role':'assistant','content':None,'tool_calls':calls},'tool_calls',None
cli.NvidiaProvider=Fake
raise SystemExit(cli.main(['--provider','nvidia','--project','.']))
'''
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            terminal = PTY([sys.executable, '-c', driver], root)
            try:
                terminal.until('›'.encode())
                terminal.send(b'run a slow command\r')
                terminal.until(b'Approve? Type yes:')
                terminal.send(b'yes\r')
                terminal.until(b'COMMAND_READY')
                terminal.send(b'followup stays pending\r')
                terminal.until(b'Queued follow-up')
                terminal.send(b'/stop\r')
                terminal.until(b'Turn interrupted.', timeout=5)
                terminal.send(b'/queue\r')
                terminal.until(b'Queued input: 1/32')
                terminal.send(b'exit\r')
                terminal.until(b'Goodbye!')
                self.assertEqual(terminal.process.wait(timeout=5), 0)
                self.assertFalse((root/'must-not-exist.txt').exists())
                session = json.loads(next((root/'.codine/sessions').glob('*.json')).read_text())
                validate_messages(session['messages'])
                results = [json.loads(row['content']) for row in session['messages'] if row['role']=='tool']
                self.assertEqual(results[0]['status'], 'cancelled')
                self.assertIn('NOT replayed', results[1]['error'])
                self.assertEqual(len(json.loads(next((root/'.codine/sessions').glob('*.inbox')).read_text())), 1)
            finally:
                terminal.close()
