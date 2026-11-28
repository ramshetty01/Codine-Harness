import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
from prompt_toolkit.document import Document
from codine.attachments import Attachments
from codine.config import Redactor
from codine.terminal import CommandCompleter
from codine.workspace import Workspace


class AttachmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.ws = Workspace(self.root, Mock(return_value=True), Mock())
        self.output = []
        self.manager = Attachments(self.ws, Redactor(['secret-value']), self.output.append)

    def test_snapshots_quoted_paths_preview_and_no_edit_permission(self):
        (self.root / 'one file.py').write_text('secret-value\nprint(1)\n')
        result = self.manager.prepare('Explain @"one file.py" and @"one file.py"')
        self.assertEqual(len(self.manager.latest), 1)
        self.assertNotIn('secret-value', result)
        self.assertIn('print(1)', result)
        self.assertIn('SHA256', self.output[0])
        self.assertEqual(self.ws.read_versions, {})
        with self.assertRaisesRegex(ValueError, 'Read'):
            self.ws.edit_file('one file.py', '1', '2')

    def test_email_escape_and_quoted_quote_filename(self):
        self.assertEqual(self.manager.prepare('mail a@b.com and @@decorator'), 'mail a@b.com and @decorator')
        name = 'a"b.txt'
        (self.root / name).write_text('quoted')
        self.assertIn('quoted', self.manager.prepare('@' + json.dumps(name)))

    def test_bounds_exclusions_symlinks_and_policy(self):
        (self.root / 'large.txt').write_text('x' * 24001)
        with self.assertRaisesRegex(ValueError, 'exceeds'):
            self.manager.prepare('@large.txt')
        for path in ('.env', '../outside', '/tmp/outside'):
            with self.assertRaises(ValueError):
                self.manager.prepare('@' + path)
        (self.root / 'link.txt').symlink_to(self.root / 'large.txt')
        with self.assertRaises(ValueError):
            self.manager.prepare('@link.txt')
        with self.assertRaises(ValueError):
            self.manager.prepare(' '.join('@file' + str(i) for i in range(9)))

    def test_completion_selects_valid_paths_without_content_read(self):
        (self.root / 'file name.txt').write_text('content')
        (self.root / '.env').write_text('secret')
        completer = CommandCompleter()
        completer.attachment_paths = self.manager.paths
        with unittest.mock.patch.object(self.ws, '_read', side_effect=AssertionError('No content reads')):
            choices = list(completer.get_completions(Document('Explain @fi'), None))
        self.assertEqual([choice.text for choice in choices], ['@"file name.txt"'])
        self.assertEqual(choices[0].start_position, -3)

    def test_explicit_read_policy_applies_to_attachments(self):
        from codine.command_policy import CommandPolicy, FileRule
        (self.root / 'private.txt').write_text('private')
        self.ws.command_policy = CommandPolicy(self.root / 'policy.json', file_rules=(
            FileRule(id='deny', action='deny', operation='read', project=str(self.root), match='exact', path='private.txt'),))
        with self.assertRaises(ValueError):
            self.manager.prepare('@private.txt')
        self.assertEqual(self.output, [])

    def test_agent_delivers_queued_attachment_only_at_safe_boundary(self):
        from codine.agent import Agent
        from codine.config import Config
        from codine.inbox import Inbox
        from codine.registry import ToolRegistry
        from codine.sessions import Session, SessionStore, validate_messages
        (self.root / 'notes.txt').write_text('attached fact')
        store = SessionStore(self.root)
        self.addCleanup(store.close)
        session = Session.create(self.root, 'offline', 'system')
        store.save(session)
        provider = Mock()
        provider.complete.return_value = ({'role': 'assistant', 'content': 'done'}, 'stop', None)
        agent = Agent(Config(self.root), provider, ToolRegistry(), store, session, Mock(), Redactor())
        agent.prepare_input = self.manager.prepare
        agent.inbox = Inbox(store, session)
        queued = agent.inbox.add('Explain @notes.txt')
        self.assertEqual(self.output, [])
        agent.run_turn(queued['text'], queued_item=queued)
        self.assertIn('attached fact', json.dumps(provider.complete.call_args.args[0]))
        self.assertEqual(agent.inbox.list(), [])
        validate_messages(session.messages)

    def test_completion_quotes_apostrophe_and_at_filename_for_roundtrip(self):
        for name in ("one'apostrophe.txt", 'one@symbol.txt'):
            (self.root / name).write_text('attached ' + name)
        completer = CommandCompleter()
        completer.attachment_paths = self.manager.paths
        choices = list(completer.get_completions(Document('Explain @one'), None))
        self.assertEqual(len(choices), 2)
        for choice in choices:
            self.assertTrue(choice.text.startswith('@"'))
            result = self.manager.prepare('Explain ' + choice.text)
            self.assertEqual(len(self.manager.latest), 1)
            self.assertIn('attached ', result)

    def test_completion_scan_failure_clears_stale_names_and_keeps_prompt_usable(self):
        completer = CommandCompleter()
        completer.attachment_paths = self.manager.paths
        self.manager._names = ['old-private-name.txt']
        for error in (ValueError('Git unavailable'), OSError('scanner failed')):
            self.manager._scanned = 0
            with unittest.mock.patch.object(self.ws, 'list_files', side_effect=error):
                self.assertEqual(list(completer.get_completions(Document('Explain @'), None)), [])
            self.assertEqual(self.manager._names, [])
            self.assertTrue(list(completer.get_completions(Document('/help'), None)))
