from pathlib import Path
import subprocess
import tempfile
import unittest
from codine.git_awareness import GitInspector
from codine.sessions import Session, SessionStore
from codine.workspace import Workspace


class GitAwarenessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.git('init', '-q')
        self.git('config', 'user.email', 'test@example.test')
        self.git('config', 'user.name', 'Test')
        (self.root / 'app.py').write_text('value = 1\n')
        self.git('add', 'app.py')
        self.git('commit', '-qm', 'initial')
        self.ws = Workspace(self.root, lambda _: True, lambda _: None, track_changes=True)
        self.session = Session.create(self.root, 'fake', 'instructions')
        self.ws.change_session_id = self.session.id
        self.inspector = GitInspector(self.ws, lambda: self.session)

    def git(self, *args):
        return subprocess.check_output(['git', *args], cwd=self.root, stderr=subprocess.PIPE)

    def test_baseline_checkpoint_and_external_edit(self):
        path = self.root / 'app.py'
        path.write_text('value = 2\n')
        self.inspector.capture('session_start')
        self.ws.read_file('app.py')
        self.ws.edit_file('app.py', '2', '3')
        row = next(x for x in self.inspector.status()['changes'] if x['path'] == 'app.py')
        self.assertTrue(row['pre_existing_at_baseline'])
        self.assertTrue(row['content_changed_since_baseline'])
        self.assertTrue(row['matches_session_file_tool_output'])
        path.write_text('value = 4\n')
        row = next(x for x in self.inspector.status()['changes'] if x['path'] == 'app.py')
        self.assertFalse(row['matches_session_file_tool_output'])
        self.assertEqual(path.read_text(), 'value = 4\n')

    def test_staged_unstaged_and_index_unchanged(self):
        path = self.root / 'app.py'
        path.write_text('value = 2\n')
        self.git('add', 'app.py')
        path.write_text('value = 3\n')
        index = (self.root / '.git/index').read_bytes()
        self.assertIn('+value = 2', self.inspector.diff('app.py', True)['diff'])
        self.assertIn('-value = 2', self.inspector.diff('app.py')['diff'])
        self.assertIn('+value = 3', self.inspector.diff('app.py')['diff'])
        self.assertEqual(index, (self.root / '.git/index').read_bytes())
        self.assertEqual(self.ws.read_versions, {})

    def test_credentials_links_and_external_helpers(self):
        (self.root / '.env').write_text('SECRET=hidden')
        (self.root / 'link.py').symlink_to(self.root / 'app.py')
        for path in ('.env', 'link.py'):
            with self.assertRaises(ValueError):
                self.inspector.diff(path)
        marker = self.root / 'executed'
        helper = self.root / 'helper.sh'
        helper.write_text('#!/bin/sh\ntouch "' + str(marker) + '"\n')
        helper.chmod(0o755)
        self.git('config', 'core.fsmonitor', str(helper))
        self.git('config', 'diff.external', str(helper))
        self.inspector.status()
        self.inspector.diff('app.py')
        self.assertFalse(marker.exists())

    def test_clean_and_process_filters_never_execute_during_status_or_capture(self):
        marker = self.root / 'FILTER_EXECUTED'
        helper = self.root / 'filter-helper.sh'
        helper.write_text('#!/bin/sh\ntouch "' + str(marker) + '"\nexit 1\n')
        helper.chmod(0o755)
        for handler in ('clean', 'process'):
            with self.subTest(handler=handler):
                self.git('config', '--remove-section', 'filter.evil') if handler == 'process' else None
                self.git('config', 'filter.evil.' + handler, str(helper))
                self.git('config', 'filter.evil.required', 'true')
                (self.root / '.gitattributes').write_text('app.py filter=evil\n')
                (self.root / 'app.py').write_text('value = 2\n')
                index = (self.root / '.git/index').read_bytes()
                config = (self.root / '.git/config').read_bytes()
                self.session.git_baseline = {}
                self.inspector.capture('session_start')
                self.assertTrue(self.inspector.status()['repository'])
                self.assertIn('+value = 2', self.inspector.diff('app.py')['diff'])
                self.assertFalse(marker.exists())
                self.assertEqual(index, (self.root / '.git/index').read_bytes())
                self.assertEqual(config, (self.root / '.git/config').read_bytes())

    def test_included_filter_and_unsupported_driver_names_fail_safely(self):
        marker = self.root / 'FILTER_EXECUTED'
        helper = self.root / 'filter-helper.sh'
        helper.write_text('#!/bin/sh\ntouch "' + str(marker) + '"\nexit 1\n')
        helper.chmod(0o755)
        included = self.root / 'included-config'
        included.write_text('[filter "Included.Driver"]\nclean = ' + str(helper) + '\nrequired = true\n')
        self.git('config', 'include.path', str(included))
        (self.root / '.gitattributes').write_text('app.py filter=Included.Driver\n')
        (self.root / 'app.py').write_text('value = 2\n')
        self.inspector.status()
        self.assertFalse(marker.exists())
        included.write_text('[filter "evil=name"]\nclean = ' + str(helper) + '\n')
        (self.root / '.gitattributes').write_text('app.py filter=evil=name\n')
        with self.assertRaisesRegex(ValueError, 'Unsupported filter driver name'):
            self.inspector.status()
        self.assertFalse(marker.exists())

    def test_session_roundtrip_and_subdirectory(self):
        self.inspector.capture()
        store = SessionStore(self.root)
        self.addCleanup(store.close)
        store.save(self.session)
        self.assertEqual(store.load(self.session.id).git_baseline, self.session.git_baseline)
        sub = self.root / 'sub'
        sub.mkdir()
        (sub / 'new.py').write_text('new\n')
        inspector = GitInspector(Workspace(sub, lambda _: True), lambda: Session.create(sub, 'fake', 'system'))
        self.assertEqual(inspector.status()['changes'][0]['path'], 'new.py')
        self.assertIn('+new', inspector.diff('new.py')['diff'])

    def test_unborn_and_deleted_file(self):
        self.git('rm', 'app.py')
        self.assertIn('-value = 1', self.inspector.diff('app.py', True)['diff'])
        with tempfile.TemporaryDirectory() as temp:
            subprocess.check_call(['git', 'init', '-q', temp])
            root = Path(temp)
            (root / 'new.py').write_text('new\n')
            inspector = GitInspector(Workspace(root, lambda _: True), lambda: Session.create(root, 'fake', 'system'))
            self.assertIn('+new', inspector.diff('new.py')['diff'])

    def test_file_policy_applies_to_index_and_automatic_hashes(self):
        from unittest.mock import Mock
        from codine.command_policy import CommandPolicy, FileRule
        (self.root / 'app.py').write_text('value = 2\n')
        rule = FileRule(id='private', action='ask', operation='read',
                        project=str(self.root), match='exact', path='app.py')
        self.ws.command_policy = CommandPolicy(self.root / 'policy.json', file_rules=(rule,))
        self.ws.policy_interactive = True
        self.ws.approve = Mock(return_value=True)
        self.inspector.capture()
        self.inspector.status()
        self.ws.approve.assert_not_called()
        self.assertIsNone(self.session.git_baseline['entries']['app.py']['sha256'])
        self.inspector.diff('app.py')
        self.ws.approve.assert_called_once()
        self.ws.approve.reset_mock()
        self.inspector.diff('app.py', staged=True)
        self.ws.approve.assert_called_once()
        deny = FileRule(id='private', action='deny', operation='read',
                        project=str(self.root), match='exact', path='app.py')
        self.ws.command_policy = CommandPolicy(self.root / 'policy.json', file_rules=(deny,))
        with self.assertRaises(ValueError):
            self.inspector.diff('app.py', staged=True)
        self.assertNotIn('app.py', [row['path'] for row in self.inspector.status()['changes']])

    def test_new_session_without_git_clears_edit_permissions(self):
        from types import SimpleNamespace
        from unittest.mock import Mock, patch
        from codine.cli import command
        store = SessionStore(self.root)
        self.addCleanup(store.close)
        agent = SimpleNamespace(session=self.session, store=store, restart_requested=False)
        agent.save = lambda: store.save(agent.session)
        agent.git_inspector = GitInspector(self.ws, lambda: agent.session, agent.save)
        self.ws.read_file('app.py')
        old = agent.session.id
        with patch('codine.git_awareness.shutil.which', return_value=None):
            command('/new', agent, self.ws, Mock())
        self.assertNotEqual(agent.session.id, old)
        self.assertEqual(self.ws.read_versions, {})
        self.assertEqual(self.ws.read_ranges, {})
