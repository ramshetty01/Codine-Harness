import copy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from codine.agent import Agent, PersistenceError, SYSTEM
from codine.config import APP_ROOT, Config, Redactor
from codine.context import ContextLimitError, compact, project_context
from codine.registry import ToolRegistry, ToolSpec
from codine.restart import restart_process
from codine.sessions import Session, SessionStore, validate_messages
from codine.terminal import Terminal
from codine.tools import build_registry, run_command
from codine.workspace import Workspace


def call(name='list_files', args=None, ident='a'):
    return {'id': ident, 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args or {})}}


def reply(*calls):
    return {'role': 'assistant', 'content': None, 'tool_calls': list(calls)}


class FakeProvider:
    def __init__(self, *answers):
        self.answers = iter(answers)
        self.requests = []

    def complete(self, messages, schemas, model, emit):
        self.requests.append(copy.deepcopy(messages))
        value = next(self.answers)
        if isinstance(value, BaseException):
            raise value
        return value, 'tool_calls' if value.get('tool_calls') else 'stop', None


class HarnessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.approvals = []
        self.ws = Workspace(self.root, lambda text: self.approvals.append(text) or True, lambda _: None, self.root)
        self.store = SessionStore(self.root)
        self.addCleanup(self.store.close)
        self.session = Session.create(self.root, 'test-model', SYSTEM)
        self.config = Config(self.root)

    def agent(self, provider):
        agent = Agent(self.config, provider, None, self.store, self.session, lambda *args: None, Redactor())
        agent.registry = build_registry(self.ws, lambda: agent.request_restart(self.ws.approve))
        return agent

    def file(self, path, text):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
        return target

    def test_nested_listing_read_search(self):
        self.file('docs/README.md', '# Guide\nneedle here\n')
        self.file('.env', 'private')
        self.file('.venv/ignore.py', 'needle')
        self.assertEqual(self.ws.list_files()['files'], ['docs/README.md'])
        self.assertIn('needle', self.ws.read_file('docs/README.md')['content'])
        self.assertEqual(self.ws.search('needle')['matches'][0]['line'], 2)

    def test_pagination_long_line_and_changed_cursor(self):
        target = self.file('large.md', 'x' * 50000 + '\nEND\n')
        page = self.ws.read_file('large.md')
        self.assertEqual(page['next_page']['start_char'], 24000)
        page2 = self.ws.read_file('large.md', **page['next_page'], expected_sha256=page['sha256'])
        page3 = self.ws.read_file('large.md', **page2['next_page'], expected_sha256=page['sha256'])
        self.assertIn('END', page3['content'])
        target.write_text('changed')
        with self.assertRaisesRegex(ValueError, 'between pages'):
            self.ws.read_file('large.md', **page['next_page'], expected_sha256=page['sha256'])

    def test_reject_escapes_secrets_symlinks_and_binary(self):
        self.file('bin', 'a\x00b')
        (self.root / 'link').symlink_to('/tmp')
        for name in ('../outside', '/tmp/file', '.env', '.git/config', 'secret.pem', 'link/x'):
            with self.subTest(path=name), self.assertRaises(ValueError):
                self.ws.read_file(name)
        with self.assertRaises(ValueError):
            self.ws.read_file('bin')

    def test_approve_decline_stale_and_unseen_edits(self):
        target = self.file('work.py', 'hello\nother\n')
        self.ws.read_file('work.py', max_lines=1)
        with self.assertRaisesRegex(ValueError, 'region'):
            self.ws.edit_file('work.py', 'other', 'changed')
        self.assertEqual(self.ws.edit_file('work.py', 'hello', 'hi')['status'], 'updated')
        self.ws.read_file('work.py')
        self.ws.approve = lambda _: False
        self.assertEqual(self.ws.edit_file('work.py', 'hi', 'no')['status'], 'declined')
        self.assertEqual(target.read_text(), 'hi\nother\n')
        target.write_text('human edit')
        with self.assertRaisesRegex(ValueError, 'unread or changed'):
            self.ws.edit_file('work.py', 'human', 'model')

    def test_edit_changed_during_approval(self):
        target = self.file('a.txt', 'before')
        self.ws.read_file('a.txt')
        def approval(_):
            target.write_text('human')
            return True
        self.ws.approve = approval
        with self.assertRaisesRegex(ValueError, 'after the preview'):
            self.ws.edit_file('a.txt', 'before', 'after')
        self.assertEqual(target.read_text(), 'human')

    def test_new_file_no_overwrite_and_core_approval(self):
        self.ws.mode = 'auto-edit'
        self.ws.write_file('nested/a.md', 'hello')
        self.assertFalse(self.approvals)
        with self.assertRaises(ValueError):
            self.ws.write_file('nested/a.md', 'overwrite')
        self.ws.write_file('main.py', 'print(1)\n')
        self.assertEqual(len(self.approvals), 1)
        self.ws.approve = lambda _: False
        self.ws.mode = 'ask'
        self.assertEqual(self.ws.write_file('declined.txt', 'no')['status'], 'declined')
        self.assertFalse((self.root / 'declined.txt').exists())

    def test_hardlink_edit_rejected(self):
        self.file('a.txt', 'original')
        os.link(self.root / 'a.txt', self.root / 'b.txt')
        self.ws.read_file('a.txt')
        with self.assertRaisesRegex(ValueError, 'Hard-linked'):
            self.ws.edit_file('a.txt', 'original', 'changed')

    def test_syntax_check_does_not_execute(self):
        self.file('a.py', 'raise RuntimeError("do not run")\n')
        self.assertTrue(self.ws.check_python('a.py')['ok'])
        self.file('a.py', 'def broken(')
        self.assertFalse(self.ws.check_python('a.py')['ok'])

    def test_registry_matches_dispatch_and_rejects_bad_arguments(self):
        registry = build_registry(self.ws, lambda: {'status': 'scheduled'})
        self.assertEqual({row['function']['name'] for row in registry.schemas()}, set(registry.tools))
        for item in (call('unknown'), call('read_file'), call('list_files', {'limit': True}), call('list_files', {'extra': 3})):
            self.assertIn('error', json.loads(registry.execute(item)))
        malformed = call(); malformed['function']['arguments'] = '['
        self.assertIn('error', registry.execute(malformed))

    def test_live_shape_tool_loop_and_session_resume(self):
        provider = FakeProvider(reply(call()), {'role': 'assistant', 'content': 'Done'})
        self.agent(provider).run_turn('list files')
        validate_messages(provider.requests[1])
        self.assertEqual(provider.requests[1][-1]['role'], 'tool')
        loaded = self.store.load(self.session.id)
        self.assertEqual(loaded.messages, self.session.messages)
        self.assertIsNone(loaded.pending)

    def test_interrupted_tool_journal_no_replay(self):
        agent = self.agent(FakeProvider(reply(call('interrupt'))))
        count = []
        def interrupt():
            count.append(1)
            raise KeyboardInterrupt()
        agent.registry.register(ToolSpec('interrupt', '', {'type': 'object'}, interrupt))
        with self.assertRaises(KeyboardInterrupt):
            agent.run_turn('do it')
        loaded = self.store.load(self.session.id)
        self.assertEqual(count, [1])
        self.assertIn('outcome unknown', loaded.messages[-1]['content'])
        validate_messages(loaded.messages)

    def test_crash_repair_preserves_known_results(self):
        self.session.messages.append({'role': 'user', 'content': 'task'})
        self.session.pending = {'reply': reply(call(ident='a'), call(ident='b')), 'results': {'a': 'completed'}}
        self.store.save(self.session)
        restored = self.store.load(self.session.id)
        self.assertEqual(restored.messages[-2]['content'], 'completed')
        self.assertIn('NOT replayed', restored.messages[-1]['content'])
        self.assertIsNone(restored.pending)

    def test_api_failure_does_not_erase_actions(self):
        agent = self.agent(FakeProvider(reply(call()), RuntimeError('offline')))
        with self.assertRaisesRegex(RuntimeError, 'offline'):
            agent.run_turn('list')
        self.assertEqual(self.store.load(self.session.id).messages[-1]['role'], 'tool')

    def test_persistence_failure_prevents_actions(self):
        agent = self.agent(FakeProvider(reply(call())))
        with patch.object(self.store, 'save', side_effect=OSError('full')), self.assertRaises(PersistenceError):
            agent.run_turn('task')
        self.assertEqual(agent.provider.requests, [])

    def test_session_redaction_corruption_workspace_and_lock(self):
        self.store.redactor = Redactor(['credential-12345'])
        self.session.messages.append({'role': 'user', 'content': 'credential-12345'})
        self.store.save(self.session)
        raw = self.store.path(self.session.id).read_text()
        self.assertNotIn('credential-12345', raw)
        other = SessionStore(self.root)
        self.addCleanup(other.close)
        with self.assertRaisesRegex(ValueError, 'already open'):
            other.load(self.session.id)
        path = self.store.path(self.session.id)
        path.write_text('{broken')
        with self.assertRaises(ValueError):
            self.store.load(self.session.id)
        self.assertEqual(path.read_text(), '{broken')
        data = json.loads(raw); data['project'] = '/different'
        path.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, 'workspace'):
            self.store.load(self.session.id)

    def test_context_trims_complete_groups_and_keeps_archive(self):
        for index in range(6):
            self.session.messages.extend([{'role': 'user', 'content': str(index) * 600}, reply(call(ident=str(index))), {'role': 'tool', 'tool_call_id': str(index), 'content': 'result'}, {'role': 'assistant', 'content': 'done'}])
        archive = copy.deepcopy(self.session.messages)
        context, start = project_context(self.session, [], 5000)
        validate_messages(context)
        self.assertGreater(start, 1)
        self.assertEqual(self.session.messages, archive)
        self.assertTrue(compact(self.session))
        self.assertEqual(len(self.session.messages), len(archive))
        with self.assertRaises(ContextLimitError):
            project_context(self.session, [], 10)

    def test_restart_requested_finishes_batch_without_next_request(self):
        provider = FakeProvider(reply(call('request_restart'), call(ident='b')))
        agent = self.agent(provider)
        agent.run_turn('restart')
        self.assertTrue(agent.restart_requested)
        self.assertEqual(len(provider.requests), 1)
        self.assertIn('skipped', self.session.messages[-1]['content'])
        validate_messages(self.session.messages)

    def test_restart_uses_same_interpreter_project_and_session(self):
        agent = self.agent(FakeProvider())
        calls = []
        restart_process(agent, execute=lambda program, argv: calls.append((program, argv)))
        self.assertEqual(calls[0][0], sys.executable)
        self.assertIn(str(APP_ROOT / 'main.py'), calls[0][1])
        self.assertIn(str(self.root.resolve()), calls[0][1])
        self.assertIn(self.session.id, calls[0][1])
        self.assertEqual(self.store.active, self.session.id)

    def test_restart_syntax_failure_does_not_execute(self):
        self.file('main.py', 'def broken(')
        self.file('codine/cli.py', '# dummy')
        with self.assertRaisesRegex(ValueError, 'Syntax error'), patch('os.execv') as execute:
            restart_process(self.agent(FakeProvider()), self.root, execute)
        execute.assert_not_called()

    def test_commands_success_nonzero_approval_and_clean_environment(self):
        with patch.dict(os.environ, {'NVIDIA_API_KEY': 'do-not-forward'}):
            result = run_command(self.ws, ['python', '-c', 'import os; print(os.getcwd()); print(os.getenv("NVIDIA_API_KEY")); import sys; print("err",file=sys.stderr); sys.exit(3)'])
        self.assertEqual(result['exit_code'], 3)
        self.assertIn(str(self.root), result['stdout'])
        self.assertIn('None', result['stdout'])
        self.assertIn('err', result['stderr'])
        self.ws.approve = lambda _: False
        self.assertEqual(run_command(self.ws, ['python', '-c', 'print(1)'])['status'], 'declined')

    def test_commands_timeout_and_output_cap(self):
        result = run_command(self.ws, ['python', '-c', 'import time; time.sleep(20)'], timeout=1)
        self.assertEqual(result['status'], 'timeout')
        self.assertLess(result['seconds'], 3)
        result = run_command(self.ws, ['python', '-c', 'while True: print("x" * 10000)'])
        self.assertEqual(result['status'], 'output_limit')
        self.assertTrue(result['output_truncated'])
        self.assertLessEqual(len(result['stdout']), 12000)

    def test_noninteractive_approval_never_reads_stdin(self):
        ui = Terminal(Redactor(), interactive=False)
        with patch('builtins.input', side_effect=AssertionError('must not read')), redirect_stdout(io.StringIO()):
            self.assertFalse(ui.approve('run?'))

    def test_stream_secret_split_and_escape_sanitized(self):
        ui = Terminal(Redactor(['secret-abcdef']), interactive=False)
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            ui.emit('text', 'hello secret-')
            ui.emit('text', 'abcdef\x1b[2J')
            ui.emit('end_stream', '')
        self.assertNotIn('secret-abcdef', buffer.getvalue())
        self.assertNotIn('\x1b', buffer.getvalue())
        self.assertIn('[REDACTED]', buffer.getvalue())

    def test_cli_check_does_not_create_session(self):
        result = subprocess.run([sys.executable, str(APP_ROOT / 'main.py'), '--check'], cwd=self.root, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        # setUp created our test store; --check should not add a JSON snapshot.
        self.assertEqual(list(self.store.directory.glob('*.json')), [])

    def test_intent_save_failure_prevents_side_effect(self):
        agent = self.agent(FakeProvider(reply(call('write_file', {'path': 'new.txt', 'content': 'no'}))))
        actual_save = self.store.save
        def fail_intent(session):
            if session.pending:
                raise OSError('disk full')
            actual_save(session)
        with patch.object(self.store, 'save', side_effect=fail_intent), self.assertRaises(PersistenceError):
            agent.run_turn('write')
        self.assertFalse((self.root / 'new.txt').exists())

    def test_result_save_failure_recovers_unknown_without_replay(self):
        agent = self.agent(FakeProvider(reply(call('write_file', {'path': 'new.txt', 'content': 'once'}))))
        actual_save = self.store.save
        def fail_result(session):
            if session.pending and session.pending['results']:
                raise OSError('disk full')
            actual_save(session)
        with patch.object(self.store, 'save', side_effect=fail_result), self.assertRaises(PersistenceError):
            agent.run_turn('write')
        self.assertEqual((self.root / 'new.txt').read_text(), 'once')
        loaded = self.store.load(self.session.id)
        self.assertIn('outcome unknown', loaded.messages[-1]['content'])
        self.assertEqual(len(self.approvals), 1)

    def test_snapshot_replace_failure_preserves_original(self):
        self.store.save(self.session)
        path = self.store.path(self.session.id)
        original = path.read_bytes()
        self.session.messages.append({'role': 'user', 'content': 'unsaved'})
        with patch('codine.sessions.os.replace', side_effect=OSError('disk error')), self.assertRaises(OSError):
            self.store.save(self.session)
        self.assertEqual(path.read_bytes(), original)
        self.assertFalse(list(self.store.directory.glob('.save-*')))

    def test_clear_keeps_archive_and_new_context_is_valid(self):
        from codine.cli import command
        agent = self.agent(FakeProvider({'role': 'assistant', 'content': 'saved'}))
        agent.run_turn('remember mango')
        archive = copy.deepcopy(self.session.messages)
        ui = Terminal(Redactor(), interactive=False)
        with redirect_stdout(io.StringIO()):
            command('/clear', agent, self.ws, ui)
        self.assertEqual(self.session.messages, archive)
        self.session.messages.append({'role': 'user', 'content': 'fresh task'})
        context, _ = project_context(self.session, [], 160000)
        self.assertEqual(len(context), 2)
        self.assertEqual(context[-1]['content'], 'fresh task')
        validate_messages(context)

    def test_command_cancellation_reaps_process(self):
        from codine import tools
        real_popen = subprocess.Popen
        processes = []
        def capture(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            processes.append(process)
            return process
        with patch.object(tools.subprocess, 'Popen', side_effect=capture), patch.object(tools.selectors.KqueueSelector, 'select', side_effect=KeyboardInterrupt()) if sys.platform == 'darwin' else patch.object(tools.selectors.EpollSelector, 'select', side_effect=KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt):
                run_command(self.ws, ['python', '-c', 'import time; time.sleep(20)'])
        self.assertIsNotNone(processes[0].poll())

    def test_timeout_stops_descendant_process(self):
        import time
        child_code = "import time; from pathlib import Path; time.sleep(2); Path('escaped.txt').write_text('bad')"
        parent_code = 'import subprocess,sys,time; subprocess.Popen([sys.executable,"-c",' + repr(child_code) + ']); time.sleep(20)'
        result = run_command(self.ws, ['python', '-c', parent_code], timeout=1)
        self.assertEqual(result['status'], 'timeout')
        time.sleep(1.3)
        self.assertFalse((self.root / 'escaped.txt').exists())


class SelfDevelopmentTests(unittest.TestCase):
    def test_agent_edits_own_registry_checks_and_loads_new_tool(self):
        import shutil
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            shutil.copy(APP_ROOT / 'main.py', root / 'main.py')
            shutil.copytree(APP_ROOT / 'codine', root / 'codine', ignore=shutil.ignore_patterns('__pycache__'))
            store = SessionStore(root)
            try:
                session = Session.create(root, 'fake', SYSTEM)
                approvals = []
                workspace = Workspace(root, lambda description: approvals.append(description) or True, lambda _: None, root)
                addition = "    add('ping', 'Return pong.', lambda: {'value': 'pong'}, {})\n    return registry"
                provider = FakeProvider(
                    reply(call('read_file', {'path': 'codine/tools.py', 'max_lines': 2000})),
                    reply(call('edit_file', {'path': 'codine/tools.py', 'old_text': '    return registry', 'new_text': addition})),
                    reply(call('check_python', {'path': 'codine/tools.py'})),
                    reply(call('request_restart')),
                )
                agent = Agent(Config(root), provider, None, store, session, lambda *args: None, Redactor())
                agent.registry = build_registry(workspace, lambda: agent.request_restart(workspace.approve))
                agent.run_turn('Add ping and restart')
                self.assertTrue(agent.restart_requested)
                self.assertNotIn('ping', agent.registry.tools)  # Running objects are still old.
                invoked = []
                restart_process(agent, root, execute=lambda *args: invoked.append(args))
                self.assertEqual(len(invoked), 1)
                probe = subprocess.run([sys.executable, '-c', 'from codine.tools import build_registry; from codine.workspace import Workspace; from pathlib import Path; import json; r=build_registry(Workspace(Path.cwd(),lambda _:False),lambda:None); print(r.execute({"id":"p","function":{"name":"ping","arguments":"{}"}}))'], cwd=root, capture_output=True, text=True)
                self.assertEqual(probe.returncode, 0, probe.stderr)
                self.assertEqual(json.loads(probe.stdout), {'value': 'pong'})
                self.assertEqual(len(approvals), 2)  # Core edit and model-requested restart.
                validate_messages(store.load(session.id).messages)
            finally:
                store.close()


if __name__ == '__main__':
    unittest.main()
