import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from codine.config import Redactor
from codine.input_settings import PrivateHistory, preferences, edit_text


class InputSettingsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.project = self.root / 'project'; self.project.mkdir()
        self.path = self.root / 'input.json'
        self.redactor = Redactor(['private-api-value'])

    def settings(self, data):
        self.path.write_text(json.dumps(data)); self.path.chmod(0o600)
        return preferences(self.project, self.path)

    def test_binding_alias_prefix_and_reserved_conflicts(self):
        self.assertEqual(preferences(self.project, self.path)['keys']['submit'], ['enter'])
        self.assertEqual(self.settings({'version': 1, 'keys': {'submit': ['c-s']}})['keys']['submit'], ['c-s'])
        for keys in ({'submit': ['c-r']}, {'submit': ['escape']}, {'submit': ['c-c']},
                     {'submit': ['c-m']}, {'submit': ['escape', 'enter']}, {'newline': ['unknown-key']}):
            with self.assertRaises(ValueError): self.settings({'version': 1, 'keys': keys})

    def test_user_settings_cannot_be_project_local_or_shared(self):
        local = self.project / 'input.json'; local.write_text('{"version":1}')
        with self.assertRaises(ValueError): preferences(self.project, local)
        self.settings({'version': 1})
        self.path.chmod(0o644)
        with self.assertRaises(ValueError): preferences(self.project, self.path)
        self.path.unlink(); self.path.symlink_to(local)
        with self.assertRaises((OSError, ValueError)): preferences(self.project, self.path)

    def test_private_history_persists_redacted_and_merges_writers(self):
        path = self.root / 'history.json'
        first = PrivateHistory(path, lambda: self.redactor)
        second = PrivateHistory(path, lambda: self.redactor)
        first.append_string('private-api-value request')
        second.append_string('second task')
        first.append_string('third task')
        self.assertNotIn('private-api-value', path.read_text())
        self.assertEqual(list(first.load_history_strings()), ['third task', 'second task', '[REDACTED] request'])
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertIsNone(first.failure)

    def test_history_corruption_links_and_atomic_failure_preserve_data(self):
        path = self.root / 'history.json'; history = PrivateHistory(path, lambda: self.redactor)
        history.append_string('first')
        before = path.read_bytes()
        with patch('codine.input_settings.os.rename', side_effect=OSError('disk error')):
            history.append_string('second')
        self.assertEqual(path.read_bytes(), before)
        self.assertIsNotNone(history.failure)
        path.write_text('corrupt data')
        history.append_string('third')
        self.assertEqual(path.read_text(), 'corrupt data')
        path.unlink(); target = self.root / 'target'; target.write_bytes(before)
        path.symlink_to(target)
        history.append_string('fourth')
        self.assertEqual(target.read_bytes(), before)
        self.assertIsNotNone(history.failure)

    def test_history_count_and_byte_bounds(self):
        path = self.root / 'history.json'; history = PrivateHistory(path, lambda: self.redactor)
        with patch('codine.input_settings.MAX_ENTRIES', 3), patch('codine.input_settings.MAX_HISTORY_BYTES', 100):
            for i in range(6): history.append_string('task' + str(i))
        entries = json.loads(path.read_text())['entries']
        self.assertEqual(entries, ['task3', 'task4', 'task5'])
        self.assertLessEqual(path.stat().st_size, 100)

    def test_editor_returns_unsent_redacted_text_and_cleans_temporary_file(self):
        capture = self.root / 'path.txt'
        script = self.root / 'editor.py'
        script.write_text('import pathlib,sys\np=pathlib.Path(sys.argv[-1])\npathlib.Path(' + repr(str(capture)) + ').write_text(str(p))\np.write_text("edited private-api-value")\n')
        result = edit_text('initial', [sys.executable, str(script)], redactor=self.redactor)
        self.assertEqual(result, 'edited [REDACTED]')
        self.assertFalse(Path(capture.read_text()).exists())
        with self.assertRaises(ValueError): edit_text('initial', None, redactor=self.redactor)

    def test_editor_failure_timeout_symlink_and_project_script_refused(self):
        for script in ('import sys;sys.exit(1)', 'import time;time.sleep(5)',
                       'import os,sys;os.unlink(sys.argv[-1]);os.symlink("/etc/passwd",sys.argv[-1])'):
            with self.assertRaises((ValueError, OSError)):
                edit_text('original', [sys.executable, '-c', script], redactor=self.redactor, timeout=.1)
        script = self.project / 'editor.py'; script.write_text('pass')
        with self.assertRaises(ValueError): self.settings({'version': 1, 'editor_argv': [sys.executable, str(script)]})

    def test_approval_uses_separate_history_and_busy_editor_is_rejected(self):
        from codine.terminal import Terminal
        terminal = Terminal(self.redactor, interactive=False)
        terminal.prompt_history = Mock()
        self.assertFalse(terminal.approve('approval'))
        terminal.prompt_history.append_string.assert_not_called()
        terminal.interactive = True; terminal.busy_reader = object()
        with self.assertRaises(ValueError): terminal.external_editor()

    def test_concurrent_lock_conflict_warns_and_preserves_prior_entries(self):
        path = self.root / 'history.json'; first = PrivateHistory(path, lambda: self.redactor)
        second = PrivateHistory(path, lambda: self.redactor)
        first.append_string('first')
        before = path.read_bytes()
        with first.locked():
            second.append_string('not silently persisted')
            self.assertIsNotNone(second.failure)
            self.assertEqual(path.read_bytes(), before)
        second.append_string('next successful write')
        self.assertEqual(list(first.load_history_strings()), ['next successful write', 'first'])

    def test_editor_timeout_kills_child_process_group(self):
        import subprocess
        pidfile = self.root / 'child.pid'
        script = self.root / 'editor-child.py'
        script.write_text('import subprocess,sys,time,pathlib\np=subprocess.Popen([sys.executable,"-c","import time;time.sleep(60)"])\npathlib.Path(' + repr(str(pidfile)) + ').write_text(str(p.pid))\ntime.sleep(60)\n')
        with self.assertRaises(ValueError):
            edit_text('draft', [sys.executable, str(script)], redactor=self.redactor, timeout=.5)
        pid = int(pidfile.read_text())
        status = subprocess.run(['ps', '-o', 'stat=', '-p', str(pid)], capture_output=True, text=True).stdout.strip()
        self.assertTrue(not status or status.startswith('Z'), status)

    def test_input_files_remain_protected_even_when_workspace_contains_them(self):
        from codine.workspace import Workspace
        settings = self.project / 'input.json'
        history = self.project / 'input-history.json'
        with patch('codine.input_settings.INPUT_PATH', settings), patch('codine.input_settings.HISTORY_PATH', history):
            workspace = Workspace(self.project, Mock(return_value=True))
            for name in ('input.json', 'input-history.json', 'input-history.json.lock'):
                with self.assertRaisesRegex(ValueError, 'protected'):
                    workspace.resolve(name)

    def test_home_project_keeps_default_prompt_without_loading_user_authority(self):
        from codine.terminal import Terminal
        terminal = Terminal(self.redactor, interactive=False)
        terminal.interactive = True
        terminal._configure_editor = Mock()
        terminal.write = Mock()
        with patch('codine.input_settings.INPUT_PATH', self.project / 'input.json'), patch('codine.input_settings.HISTORY_PATH', self.project / 'history.json'), patch('codine.input_settings.preferences') as read:
            terminal.configure_input(self.project)
        read.assert_not_called()
        terminal._configure_editor.assert_called_once_with()
        self.assertIsNone(terminal.input_preferences)
        self.assertIsNone(terminal.prompt_history)
        self.assertIn('disabled', terminal.write.call_args.args[0])

    def test_real_terminal_launches_when_project_is_home(self):
        from tests.test_terminal_integration import PTY
        from codine.config import APP_ROOT
        terminal = PTY([sys.executable, str(APP_ROOT / 'main.py'), '--provider', 'nvidia', '--project', str(self.project)], self.project, {'HOME': str(self.project)})
        try:
            terminal.until(b'persistent history disabled')
            terminal.until('›'.encode())
            terminal.send(b'/help\r')
            terminal.until(b'Show this guide')
            terminal.send(b'exit\r')
            terminal.until(b'Goodbye!')
            self.assertEqual(terminal.process.wait(timeout=5), 0)
            self.assertFalse((self.project / '.local/state/codine/input-history.json').exists())
        finally:
            terminal.close()
