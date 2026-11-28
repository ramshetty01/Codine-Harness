import io
import json
from pathlib import Path
from queue import Queue
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from codine.agent import Agent
from codine.config import Config, Redactor
from codine.inbox import Inbox
from codine.sessions import Session, SessionStore
from codine.status_view import snapshot, lines
from codine.terminal import Terminal


class StatusTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name).resolve()
        self.store=SessionStore(self.root); self.addCleanup(self.store.close)
        self.session=Session.create(self.root,'fake','system')
        self.provider=Mock()
        self.agent=Agent(Config(self.root),self.provider,Mock(),self.store,self.session,Mock(),Redactor())
        self.agent.inbox=Inbox(self.store,self.session)
        self.ui=Terminal(Redactor(),interactive=False)
        self.workspace=SimpleNamespace(mode='plan',executor='docker')

    def status(self):
        return snapshot(self.agent,self.workspace,self.ui,'docker')

    def test_idle_deferred_queue_commands_and_real_phase(self):
        inbox=self.agent.inbox
        inbox.add('followup'); inbox.add('steering','steer'); inbox.defer_pending()
        self.ui.submit_command('/model')
        state=self.status()
        self.assertEqual(state['phase'],'idle')
        self.assertEqual(state['queue'],{'total':2,'ready':0,'deferred':2,'steer':1,'followup':1,'commands':1})
        self.agent._in_turn=True; self.agent._phase='provider'
        self.assertEqual(self.status()['phase'],'provider')
        self.ui._approval_pending=True
        self.assertEqual(self.status()['phase'],'REVIEW APPROVAL')
        inbox.cancel.set()
        self.assertEqual(self.status()['phase'],'STOP REQUESTED')
        self.agent._in_turn=False; self.ui._approval_pending=False
        self.assertEqual(self.status()['phase'],'idle')  # cancellation request is not proof of completion

    def test_context_cached_from_request_not_cumulative_usage_or_files(self):
        self.session.usage['total']=12345678
        self.provider.complete.return_value=({'role':'assistant','content':'ok'},'stop',None)
        self.agent.complete([{'role':'user','content':'hello'}],[],None)
        before=self.status()['context']
        self.assertLess(before['input'],12345678)
        self.assertEqual(before['output'],4096)
        self.assertEqual(before['limit'],65536)
        with patch('codine.router_catalog.metadata',side_effect=AssertionError('UI API access')), patch('pathlib.Path.open',side_effect=AssertionError('UI filesystem access')):
            self.assertEqual(self.status()['context'],before)
            self.assertIn('last prepared','\n'.join(lines(self.status())))
        self.session.model='changed'
        self.assertIsNone(self.status()['context'])

    def test_cost_unknown_pending_and_known_zero(self):
        self.assertIsNone(self.status()['cost']['spent'])
        self.session.billing={'spent':'0','limit':'3.50','pending':'0.25','blocked':''}
        output='\n'.join(lines(self.status()))
        self.assertIn('cost $0 · reserved $0.25, unsettled',output)
        self.assertIn('not a measured charge',output)
        self.session.billing['pending']=None
        self.session.billing['blocked']='stop'
        self.assertIn('BLOCKED','\n'.join(lines(self.status())))

    def test_toolbar_never_waits_for_inbox_write_lock(self):
        self.agent.inbox.add('later'); self.status()
        started=threading.Event(); release=threading.Event()
        def hold():
            with self.agent.inbox.lock:
                started.set(); release.wait(3)
        thread=threading.Thread(target=hold); thread.start(); started.wait(1)
        try:
            state=self.status()
            self.assertTrue(state['queue']['stale'])
            self.assertEqual(state['queue']['total'],1)
            self.assertFalse(release.is_set())
        finally:
            release.set(); thread.join(2)

    def test_approval_flag_always_cleared(self):
        def approve(description):
            self.assertEqual(self.status()['phase'],'REVIEW APPROVAL')
            raise KeyboardInterrupt()
        self.ui._approve=approve
        with self.assertRaises(KeyboardInterrupt): self.ui.approve('test')
        self.assertFalse(self.ui._approval_pending)

    def test_status_command_full_details_without_network(self):
        from codine.cli import command
        output=io.StringIO()
        from contextlib import redirect_stdout
        with redirect_stdout(output): command('/status',self.agent,self.workspace,self.ui)
        self.assertIn('no prepared snapshot',output.getvalue())
        self.assertIn('local session accounting only',output.getvalue())
        self.assertIn('0 deferred',output.getvalue())

    def test_narrow_footer_keeps_policy_and_separate_context_cost_rows(self):
        from prompt_toolkit.formatted_text import fragment_list_to_text
        from prompt_toolkit.utils import get_cwidth
        import os
        self.ui.state=self.status
        with patch('codine.terminal.shutil.get_terminal_size',return_value=os.terminal_size((28,24))):
            output=fragment_list_to_text(self.ui.toolbar())
        rows=output.splitlines()
        self.assertIn('PLAN',rows[0]); self.assertIn('read only',rows[0])
        self.assertTrue(rows[2].startswith('ctx est'))
        self.assertTrue(rows[3].startswith('cost'))
        self.assertTrue(all(get_cwidth(row)<=26 for row in rows))

    def test_pty_cancelled_followup_stays_visible_idle_and_status_unclipped(self):
        import sys
        from tests.test_terminal_integration import PTY
        driver = """
import time
from pathlib import Path
from codine import cli
from codine.config import Config
from prompt_toolkit.output.vt100 import Vt100_Output
# PTY fixture reports available rows; no emulator is present to answer CPR.
Vt100_Output.get_rows_below_cursor_position=lambda self: 12
cli.Config.load=staticmethod(lambda project,provider=None,model=None: Config(Path(project).resolve(),api_key='fake',model='fake'))
class Fake:
    def __init__(self,config): pass
    def close(self): pass
    def complete(self,messages,schemas,model,emit):
        emit('text','MODEL_READY')
        while True:
            emit('text','')
            time.sleep(.02)
cli.NvidiaProvider=Fake
raise SystemExit(cli.main(['--project','.']))
"""
        self.store.close()
        terminal=PTY([sys.executable,'-c',driver],self.root,columns=52,env={'HOME':str(self.root)})
        try:
            terminal.until('›'.encode()); terminal.send(b'first task\r')
            terminal.until(b'MODEL_READY'); terminal.send(b'follow later\r')
            terminal.until(b'Queued follow-up'); terminal.send(b'/stop\r')
            terminal.until(b'Cancellation requested')
            terminal.until(b'Q1 D1 C0')
            terminal.send(b'/status\r')
            terminal.until(b'queued 1: 0 ready, 1 deferred')
            terminal.until(b'last prepared')
            terminal.until(b'local session accounting only')
            terminal.send(b'exit\r'); terminal.until(b'Goodbye!')
            self.assertEqual(terminal.process.wait(timeout=5),0)
        finally:
            terminal.close()


if __name__=='__main__': unittest.main()
