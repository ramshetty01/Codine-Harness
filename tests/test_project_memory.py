import copy
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from codine.agent import Agent
from codine.config import Redactor
from codine.project_memory import ProjectMemory, command, register_memory_tool, digest
from codine.tools import build_registry
from codine.workspace import Workspace


class ProjectMemoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.redactor = Redactor(['known-private-value'])
        self.memory = ProjectMemory(self.root, self.redactor)
        self.session = SimpleNamespace(id='session-one', messages=[{'role':'user','content':'remember facts'}])
        self.agent = SimpleNamespace(project_memory=self.memory, redactor=self.redactor, session=self.session,
                                     provider=Mock(), config=SimpleNamespace(project=self.root))
        self.ws = Workspace(self.root, Mock(return_value=True))
        self.ui = SimpleNamespace(interactive=True, write=Mock(), approve=Mock(return_value=True))
        self.registry = build_registry(self.ws, lambda: None)
        register_memory_tool(self.registry, self.agent)

    def propose(self, text='Tests use unittest.'):
        call = {'function': {'name':'propose_memory','arguments':json.dumps({'text':text})}}
        return json.loads(self.registry.execute(call))

    def test_model_proposal_survives_restart_inactive_and_has_automatic_provenance(self):
        result = self.propose('known-private-value sk-abcdefghijk secret="hidden"')
        self.assertEqual(result['status'], 'proposed')
        self.assertEqual(self.memory.context(), '')
        raw = (self.root / '.codine/memory.json').read_text()
        for secret in ('known-private-value','sk-abcdefghijk','hidden'):
            self.assertNotIn(secret, raw)
        restarted = ProjectMemory(self.root, self.redactor)
        row = restarted.snapshot()['records'][0]
        self.assertEqual(row['source']['session'], 'session-one')
        self.assertEqual(row['source']['kind'], 'model_proposal')
        self.assertEqual(row['sha256'], digest(row['text']))
        self.assertEqual(restarted.context(), '')
        self.agent.project_memory = restarted
        command('approve ' + result['id'], self.agent, self.ws, self.ui)
        shown = '\n'.join(str(c.args[0]) for c in self.ui.write.call_args_list)
        self.assertIn('"status": "proposed"', shown)
        self.assertIn('"status": "approved"', shown)
        self.assertIn(row['source']['message_sha256'], shown)
        self.assertIn('Lowest precedence', restarted.context())
        self.assertEqual(set(self.registry.tools).intersection({'approve_memory','delete_memory','edit_memory'}), set())
        self.agent.provider.assert_not_called()

    def test_decline_noninteractive_and_plan_cannot_activate(self):
        ident = self.propose()['id']
        before = self.memory.snapshot()
        self.ui.approve.return_value = False
        command('approve ' + ident, self.agent, self.ws, self.ui)
        self.assertEqual(before, self.memory.snapshot())
        self.ui.interactive = False
        with self.assertRaisesRegex(ValueError, 'interactive'):
            command('approve ' + ident, self.agent, self.ws, self.ui)
        self.ui.interactive = True; self.ws.mode = 'plan'
        with self.assertRaisesRegex(ValueError, 'plan mode'):
            command('approve ' + ident, self.agent, self.ws, self.ui)
        self.assertIn('error', self.propose('not saved'))
        self.assertEqual(before, self.memory.snapshot())

    def test_edit_delete_fresh_context_and_no_budget_session_mutation(self):
        self.agent.instruction_manager = SimpleNamespace(context=Mock(return_value=('Guidance', [])))
        original = copy.deepcopy(self.session.__dict__)
        command('add "Use Python 3."', self.agent, self.ws, self.ui)
        ident = self.memory.snapshot()['records'][0]['id']
        self.assertIn('Use Python 3.', Agent.instruction_context(self.agent)[0])
        command('edit ' + ident + ' "Use Python 3.12."', self.agent, self.ws, self.ui)
        self.assertIn('Use Python 3.12.', Agent.instruction_context(self.agent)[0])
        self.assertNotIn('\"Use Python 3.\"', Agent.instruction_context(self.agent)[0])
        command('delete ' + ident, self.agent, self.ws, self.ui)
        self.assertEqual(Agent.instruction_context(self.agent), ('Guidance', []))
        self.assertEqual(self.session.__dict__, original)
        self.agent.provider.assert_not_called()

    def test_pending_edit_stays_pending_and_stale_review_never_overwrites(self):
        ident = self.propose()['id']
        command('edit ' + ident + ' "Edited inactive fact"', self.agent, self.ws, self.ui)
        self.assertEqual(self.memory.context(), '')
        def concurrent(_):
            self.memory.propose(self.agent, 'Concurrent fact')
            return True
        self.ui.approve.side_effect = concurrent
        with self.assertRaisesRegex(ValueError, 'changed after review'):
            command('approve ' + ident, self.agent, self.ws, self.ui)
        data = self.memory.snapshot()
        self.assertEqual(len(data['records']), 2)
        self.assertTrue(all(row['status'] == 'proposed' for row in data['records']))
        self.assertEqual(data['records'][0]['text'], 'Edited inactive fact')

    def test_private_state_excluded_and_project_identity_validated(self):
        self.propose()
        path = self.root / '.codine/memory.json'
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        for operation in ('read','write'):
            with self.assertRaises(ValueError): self.ws.resolve('.codine/memory.json', operation=operation)
        other = self.root / 'other'; other.mkdir()
        memory = ProjectMemory(other, self.redactor)
        self.assertEqual(memory.snapshot()['records'], [])
        (other / '.codine').mkdir(mode=0o700)
        destination = other / '.codine/memory.json'
        destination.write_bytes(path.read_bytes()); destination.chmod(0o600)
        with self.assertRaisesRegex(ValueError, 'identity'): memory.snapshot()

    def test_symlink_hardlink_shared_mode_and_invalid_schema_fail_closed(self):
        self.propose(); path = self.root / '.codine/memory.json'
        original = path.read_bytes()
        alias = self.root / 'alias'; os.link(path, alias)
        with self.assertRaises(ValueError): self.memory.snapshot()
        alias.unlink()
        path.chmod(0o644)
        with self.assertRaises(ValueError): self.memory.snapshot()
        path.chmod(0o600)
        path.unlink(); path.symlink_to(self.root / 'missing')
        with self.assertRaises(OSError): self.memory.snapshot()
        path.unlink(); path.write_bytes(original); path.chmod(0o600)
        data = json.loads(original); data['records'][0]['text'] = 'tampered without hash'
        path.write_text(json.dumps(data))
        with self.assertRaises(ValueError): self.memory.context()

    def test_bounds_and_bad_arguments_do_not_change_store(self):
        for value in ('', 'a'*8001, '\x1b[2J', 42):
            with self.assertRaises(ValueError): self.memory.propose(self.agent, value)
        self.assertEqual(self.memory.snapshot()['records'], [])
        self.propose('a'*7900); self.propose('b'*7900); self.propose('c'*7900)
        before = self.memory.snapshot()
        with self.assertRaisesRegex(ValueError, '32 KB'): self.memory.propose(self.agent, 'd'*7900)
        self.assertEqual(self.memory.snapshot(), before)
        for text in ('approve ../memory', 'add {"text":"no"}', 'edit missing "no"', 'clear'):
            with self.assertRaises(ValueError): command(text, self.agent, self.ws, self.ui)
        self.assertEqual(self.memory.snapshot(), before)

    def test_record_count_and_lock_link_boundary(self):
        before = self.memory.snapshot(); after = copy.deepcopy(before)
        for _ in range(101):
            after['records'].append(self.memory.record('x', self.memory.source(self.agent, 'model_proposal'), 'proposed'))
        after['revision'] += 1
        with self.assertRaises(ValueError): self.memory.commit(before, after)
        self.memory.root.mkdir(mode=0o700, exist_ok=True)
        (self.memory.root / 'memory.lock').symlink_to(self.root / 'other')
        self.assertIn('error', self.propose())
        self.assertFalse((self.root / 'other').exists())


class ProjectMemoryTerminalTests(unittest.TestCase):
    def test_real_prompt_review_and_delete_change_next_request_without_replay(self):
        import sys
        from tests.test_terminal_integration import PTY
        driver = r'''
import json
from codine import cli
class Fake:
    def __init__(self, config): self.calls=0
    def close(self): pass
    def complete(self, messages, schemas, model, emit):
        self.calls += 1
        context='\n'.join(m.get('content') or '' for m in messages if m['role']=='system')
        if self.calls == 1:
            return {'role':'assistant','content':None,'tool_calls':[{'id':'memory-1','type':'function','function':{'name':'propose_memory','arguments':json.dumps({'text':'PROJECT_MEMORY_FACT_ALPHA'})}}]}, 'tool_calls', None
        if self.calls == 2:
            assert 'PROJECT_MEMORY_FACT_ALPHA' not in context
            text='INACTIVE_PROPOSAL_OK'
        elif self.calls == 3:
            assert 'PROJECT_MEMORY_FACT_ALPHA' in context and 'Lowest precedence' in context
            text='APPROVED_CONTEXT_OK'
        else:
            assert self.calls == 4 and 'PROJECT_MEMORY_FACT_ALPHA' not in context
            text='DELETED_CONTEXT_OK'
        emit('text',text)
        return {'role':'assistant','content':text}, 'stop', None
cli.NvidiaProvider=Fake
raise SystemExit(cli.main(['--provider','nvidia']))
'''
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            terminal = PTY([sys.executable, '-c', driver], root)
            try:
                terminal.until('›'.encode())
                terminal.send(b'Propose one project fact.\r')
                terminal.until(b'INACTIVE_PROPOSAL_OK')
                path = root / '.codine/memory.json'
                data = json.loads(path.read_text()); ident = data['records'][0]['id']
                self.assertEqual(data['records'][0]['status'], 'proposed')
                offset = len(terminal.output)
                terminal.send(('/memory approve ' + ident + '\r').encode())
                terminal.until(b'Apply memory approve', start=offset)
                terminal.until(b'Approve? Type yes:', start=terminal.output.index(b'Apply memory approve', offset))
                self.assertEqual(json.loads(path.read_text())['records'][0]['status'], 'proposed')
                terminal.send(b'yes\r'); terminal.until(b'Memory updated.', start=offset)
                terminal.send(b'Check current context.\r'); terminal.until(b'APPROVED_CONTEXT_OK')
                offset = len(terminal.output)
                terminal.send(('/memory delete ' + ident + '\r').encode())
                terminal.until(b'Apply memory delete', start=offset)
                terminal.until(b'Approve? Type yes:', start=terminal.output.index(b'Apply memory delete', offset))
                terminal.send(b'yes\r'); terminal.until(b'Memory updated.', start=offset)
                terminal.send(b'Check context again.\r'); terminal.until(b'DELETED_CONTEXT_OK')
                self.assertEqual(json.loads(path.read_text())['records'], [])
                terminal.send(b'/quit\r'); terminal.process.wait(timeout=10)
                self.assertEqual(terminal.process.returncode, 0)
            finally:
                terminal.close()


if __name__ == '__main__':
    unittest.main()
