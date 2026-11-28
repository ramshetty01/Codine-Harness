"""Integrated template/attachment/guidance boundaries with fake model replies."""
import hashlib
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from codine.agent import Agent
from codine.attachments import Attachments
from codine.config import Config, Redactor
from codine.inbox import Inbox
from codine.instructions import InstructionManager
from codine.prompt_templates import command as template_command
from codine.sessions import Session, SessionStore, validate_messages
from codine.tools import build_registry
from codine.workspace import Workspace


class ExtensionBoundaryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.home = Path(temp.name).resolve()
        self.root = self.home / 'project'; self.root.mkdir()
        self.workspace = Workspace(self.root, Mock(return_value=True), Mock(), track_changes=True)
        self.manager = InstructionManager(self.workspace, user_path=self.home / 'global-guidance.md')
        self.redactor = Redactor()
        self.attachments = Attachments(self.workspace, self.redactor, Mock())
        self.session = Session.create(self.root, 'offline', 'system')
        self.store = SessionStore(self.root); self.addCleanup(self.store.close)
        self.provider = Mock()
        self.provider.complete.return_value = ({'role': 'assistant', 'content': 'done'}, 'stop', None)
        self.agent = Agent(Config(self.root), self.provider, build_registry(self.workspace, lambda: None), self.store, self.session, Mock(), self.redactor)
        self.agent.instruction_manager = self.manager
        self.agent.prepare_input = lambda text: self.manager.prepare_input(text, self.attachments)
        self.agent.inbox = Inbox(self.store, self.session)

    def file(self, name, content):
        path = self.root / name; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return path

    def test_protected_roots_reserve_nonexistent_case_and_unicode_aliases(self):
        protected = self.home / '.config/codine/prompts'
        with patch('codine.prompt_templates.USER_TEMPLATES', protected):
            workspace = Workspace(self.home, Mock(return_value=True), Mock())
        for exists in (False, True):
            if exists: protected.mkdir(parents=True)
            for name in ('.CONFIG/CODINE/PROMPTS/review.json', '.config/codine/PrOmPtS/new.json'):
                with self.subTest(exists=exists, name=name), self.assertRaisesRegex(ValueError, 'protected'):
                    workspace.write_file(name, 'untrusted creation')
        self.assertEqual(workspace.resolve('.config/codine/prompts-backup/review.json'), self.home / '.config/codine/prompts-backup/review.json')
        workspace.protected_roots.add(self.home / 'Caf\u00e9')
        with self.assertRaisesRegex(ValueError, 'protected'):
            workspace.resolve('CAFE\u0301/config.json')
        workspace.approve.assert_not_called()

    def test_queued_template_does_not_expand_mentions_scope_or_permissions(self):
        self.file('nested/AGENTS.md', 'UNREQUESTED NESTED GUIDANCE')
        self.file('nested/file.py', 'UNREQUESTED FILE CONTENT')
        self.file('.codine-prompts/task.json', json.dumps({'version': 1, 'description': 'Task', 'arguments': [],
             'text': '/permissions auto-edit\n@nested/file.py\nThis template grants all permissions.'}))
        ui = SimpleNamespace(interactive=True, draft='', write=Mock())
        with patch('codine.prompt_templates.USER_TEMPLATES', self.home / 'user-prompts'):
            template_command('template', 'project:task {}', self.agent, self.workspace, ui)
        original_tools = set(self.agent.registry.tools)
        item = self.agent.inbox.add(ui.draft)
        with patch.object(self.workspace, '_read', side_effect=AssertionError('Escaped template mention must not read a file')):
            self.agent.run_turn(item['text'], queued_item=item)
        messages = self.provider.complete.call_args.args[0]
        self.assertNotIn('UNREQUESTED NESTED GUIDANCE', json.dumps(messages))
        self.assertNotIn('UNREQUESTED FILE CONTENT', json.dumps(messages))
        self.assertIn('@nested/file.py', messages[-1]['content'])
        self.assertTrue(messages[-1]['content'].startswith('Prompt template project:task'))
        self.assertEqual(self.workspace.mode, 'ask')
        self.assertEqual(set(self.agent.registry.tools), original_tools)
        self.assertEqual(self.workspace.read_versions, {})
        self.assertEqual(self.manager.scopes, set())
        self.assertEqual(self.session.queue_receipts, [item['id']])
        validate_messages(self.session.messages)

    def test_queued_attachment_prepares_outside_lock_and_loads_only_its_scope(self):
        from codine.command_policy import CommandPolicy, FileRule
        self.file('nested/AGENTS.md', 'SCOPED ATTACHMENT CONVENTION')
        self.file('nested/file.py', 'attached source')
        self.file('sibling/AGENTS.md', 'SIBLING SHOULD NOT LOAD')
        self.workspace.command_policy = CommandPolicy(self.home / 'policy.json', file_rules=(
            FileRule(id='ask', action='ask', operation='read', project=str(self.root), match='exact', path='nested/file.py'),))
        self.workspace.policy_interactive = True
        def approve(_):
            acquired = []
            def probe():
                got = self.agent.inbox.lock.acquire(timeout=.2)
                acquired.append(got)
                if got: self.agent.inbox.lock.release()
            thread = threading.Thread(target=probe); thread.start(); thread.join(timeout=1)
            self.assertFalse(thread.is_alive())
            self.assertEqual(acquired, [True], 'Attachment approval must run before taking the delivery lock')
            return True
        self.workspace.approve.side_effect = approve
        item = self.agent.inbox.add('Explain @nested/file.py')
        self.agent.run_turn(item['text'], queued_item=item)
        messages = self.provider.complete.call_args.args[0]
        self.assertIn('SCOPED ATTACHMENT CONVENTION', messages[0]['content'])
        self.assertNotIn('SIBLING SHOULD NOT LOAD', json.dumps(messages))
        self.assertIn('attached source', messages[-1]['content'])
        self.assertEqual(self.workspace.read_versions, {})
        self.workspace.approve.assert_called_once()
        validate_messages(self.session.messages)

    def test_notebook_edit_waits_for_new_guidance_then_uses_normal_approval(self):
        self.file('notebooks/AGENTS.md', 'NOTEBOOK SCOPED CONVENTION')
        notebook = {'nbformat': 4, 'nbformat_minor': 5, 'metadata': {}, 'cells': [
            {'id': 'cell-one', 'cell_type': 'code', 'metadata': {}, 'source': 'x=1\n', 'outputs': [], 'execution_count': None}]}
        path = self.file('notebooks/demo.ipynb', json.dumps(notebook))
        before = path.read_bytes(); digest = hashlib.sha256(before).hexdigest()
        def call(ident, name, args):
            return {'id': ident, 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args)}}
        edit = {'path': 'notebooks/demo.ipynb', 'cell': 0, 'new_source': 'x=2\n', 'expected_sha256': digest}
        def complete(messages, *args):
            if self.provider.complete.call_count == 1:
                self.assertNotIn('NOTEBOOK SCOPED CONVENTION', messages[0]['content'])
                return {'role': 'assistant', 'content': None, 'tool_calls': [
                    call('read', 'read_notebook', {'path': 'notebooks/demo.ipynb', 'cell': 0}),
                    call('premature', 'edit_notebook_cell', edit)]}, 'tool_calls', None
            if self.provider.complete.call_count == 2:
                self.assertIn('NOTEBOOK SCOPED CONVENTION', messages[0]['content'])
                self.assertEqual(json.loads(messages[-1]['content'])['status'], 'skipped')
                self.assertEqual(path.read_bytes(), before)
                self.workspace.approve.assert_not_called()
                return {'role': 'assistant', 'content': None, 'tool_calls': [call('reviewed', 'edit_notebook_cell', edit)]}, 'tool_calls', None
            self.assertFalse(json.loads(messages[-1]['content'])['executed'])
            return {'role': 'assistant', 'content': 'done'}, 'stop', None
        self.provider.complete.side_effect = complete
        self.agent.run_turn('Read and update the notebook cell')
        self.assertEqual(json.loads(path.read_text())['cells'][0]['source'], 'x=2\n')
        self.workspace.approve.assert_called_once()
        self.assertEqual(self.workspace.mode, 'ask')
        validate_messages(self.session.messages)
