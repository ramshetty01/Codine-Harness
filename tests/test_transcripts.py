import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from codine.config import Redactor
from codine.sessions import Session, SessionStore
from codine.transcripts import command, portable, sanitized, validate, read_document


class TranscriptTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.redactor = Redactor(['secret-known-value'])
        self.session = Session.create(self.root, 'test-model', 'current system', provider='openrouter')
        self.session.messages.extend([
            {'role': 'user', 'content': 'secret-known-value sk-example123456789'},
            {'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'call1', 'type': 'function', 'function': {'name': 'read_file', 'arguments': '{"path":"a.py", "password":"private-value"}'}}]},
            {'role': 'tool', 'tool_call_id': 'call1', 'content': 'old file'},
            {'role': 'assistant', 'content': 'done', 'reasoning_details': [{'secret': 'opaque'}]},
        ])
        self.store = SessionStore(self.root, self.redactor)
        self.agent = SimpleNamespace(session=self.session, store=self.store, redactor=self.redactor, restart_requested=True)
        self.agent.save = lambda: self.store.save(self.agent.session)
        self.workspace = SimpleNamespace(root=self.root, read_versions={'old': 1}, read_ranges={'old': 1}, lifecycle_versions={'old': 1}, restart_requested=True)
        self.output = []
        self.ui = SimpleNamespace(interactive=True, write=self.output.append)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def run_command(self, kind, arg):
        command(kind, arg, self.agent, self.workspace, self.ui, 'CURRENT TRUSTED SYSTEM')

    def confirm(self, kind):
        self.run_command(kind, 'confirm ' + self.agent._transcript_review['digest'])

    def test_export_requires_review_redacts_and_excludes_authority(self):
        self.run_command('export', 'out.json')
        self.assertFalse((self.root / 'out.json').exists())
        preview = self.agent._transcript_review['raw'].decode()
        for secret in ('secret-known-value', 'sk-example123456789', 'private-value', 'opaque'):
            self.assertNotIn(secret, preview)
        self.confirm('export')
        document = read_document(self.root / 'out.json')
        self.assertEqual(set(document), {'format', 'version', 'title', 'messages'})
        self.assertEqual((self.root / 'out.json').stat().st_mode & 0o777, 0o600)

    def test_import_fresh_owner_current_instructions_preserves_cost(self):
        self.run_command('export', 'out.json'); self.confirm('export')
        self.session.billing = {'spent': '0.5', 'pending': None, 'blocked': None, 'shared_request': 'settled-id', 'shared_ledger': 'ledger'}
        ledger = self.root / 'shared.json'; ledger.write_text('durable-shared-state')
        self.agent.shared_budget_path = ledger
        self.run_command('import', 'out.json')
        old_id = self.agent.session.id
        self.confirm('import')
        result = self.agent.session
        self.assertNotEqual(result.id, old_id)
        self.assertEqual(result.messages[0]['content'], 'CURRENT TRUSTED SYSTEM')
        self.assertEqual(result.billing['spent'], '0.5')
        self.assertNotIn('shared_request', result.billing)
        self.assertEqual(ledger.read_text(), 'durable-shared-state')
        self.assertFalse(self.workspace.read_versions)
        self.assertFalse(self.agent.restart_requested)
        self.assertIsNone(result.pending)
        self.assertEqual(result.execution, self.session.execution)

    def test_pending_uncertain_receipts_block_without_mutation(self):
        for billing in ({'pending': '0.25', 'shared_request': 'uncertain'}, {'blocked': 'unknown'}):
            self.agent.session.billing = billing
            before = copy.deepcopy(billing)
            with self.assertRaises(ValueError): self.run_command('export', 'out.json')
            with self.assertRaises(ValueError): self.run_command('import', 'out.json')
            self.assertEqual(self.agent.session.billing, before)
        self.agent.session.billing = {}
        self.agent.session.pending = {'reply': 'unresolved'}
        with self.assertRaises(ValueError): self.run_command('export', 'out.json')

    def test_schema_pairs_version_and_oversize(self):
        original = portable(self.session)
        for mutate in (lambda d: d.update(version=True), lambda d: d.update(billing={}), lambda d: d['messages'].pop(3), lambda d: d['messages'][1].update(content='x' * 1_000_001)):
            document = copy.deepcopy(original); mutate(document)
            with self.assertRaises(ValueError): validate(document)

    def test_symlink_duplicate_keys_and_noninteractive(self):
        path = self.root / 'target.json'; path.write_text('{"version":1,"version":2}')
        with self.assertRaises(ValueError): read_document(path)
        link = self.root / 'link.json'; link.symlink_to(path)
        with self.assertRaises(OSError): read_document(link)
        self.ui.interactive = False
        with self.assertRaises(ValueError): self.run_command('export', 'out.json')
        self.assertFalse((self.root / 'out.json').exists())

    def test_digest_and_destination_races_fail_closed(self):
        self.run_command('export', 'out.json')
        with self.assertRaises(ValueError): self.run_command('export', 'confirm wrong')
        target = self.root / 'out.json'; target.write_text('original')
        with self.assertRaises(FileExistsError): self.confirm('export')
        self.assertEqual(target.read_text(), 'original')

    def test_import_confirm_uses_reviewed_snapshot_not_changed_source(self):
        self.run_command('export', 'out.json'); self.confirm('export')
        self.run_command('import', 'out.json')
        (self.root / 'out.json').write_text('malicious changed source')
        self.confirm('import')
        self.assertEqual(self.agent.session.messages[3]['content'], 'old file')

    def test_import_captures_fresh_git_baseline_before_later_edits(self):
        import subprocess
        from codine.git_awareness import GitInspector
        from codine.workspace import Workspace
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        path = self.root / 'app.py'
        path.write_text('before import')
        self.workspace = Workspace(self.root, lambda _: True)
        self.agent.git_inspector = GitInspector(self.workspace, lambda: self.agent.session, self.agent.save)
        self.run_command('export', 'out.json'); self.confirm('export')
        self.run_command('import', 'out.json'); self.confirm('import')
        baseline = self.agent.session.git_baseline
        self.assertEqual(baseline['reason'], 'session_start')
        self.assertIn('app.py', baseline['entries'])
        path.write_text('after import')
        row = next(row for row in self.agent.git_inspector.status()['changes'] if row['path'] == 'app.py')
        self.assertTrue(row['pre_existing_at_baseline'])
        self.assertTrue(row['content_changed_since_baseline'])

    def test_git_capture_error_does_not_undo_import_or_restart_reset(self):
        from unittest.mock import Mock
        from codine.git_awareness import GitError
        self.agent.git_inspector = Mock()
        self.agent.git_inspector.capture.side_effect = GitError('Git unavailable')
        self.run_command('export', 'out.json'); self.confirm('export')
        self.run_command('import', 'out.json'); self.confirm('import')
        self.assertNotEqual(self.agent.session.id, self.session.id)
        self.assertEqual(self.workspace.lifecycle_versions, {})
        self.assertFalse(self.agent.restart_requested)
        self.assertIn('Git unavailable', self.output)
