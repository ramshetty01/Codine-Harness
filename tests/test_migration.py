import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from codine.budget import Budget
from codine.migration import migrate
from codine.sessions import Session, SessionStore


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.old = self.base / 'old project'
        self.old.mkdir()
        self.source = Session.create(self.old, 'fake', 'old system', provider='openrouter')
        self.source.messages.extend([{'role': 'user', 'content': 'remember context'},
                                     {'role': 'assistant', 'content': 'preserved'}])
        self.source.billing = Budget.create().state
        self.source.billing['spent'] = '0.20'
        self.source.billing.update(shared_request='settled-id', shared_ledger='old-ledger')
        self.source.execution = {'executor': 'docker', 'image': 'sha256:' + '1' * 64}
        store = SessionStore(self.old)
        store.save(self.source)
        store.close()
        self.new = self.base / 'new project'
        self.old.rename(self.new)
        self.path = self.new / '.codine/sessions' / (self.source.id + '.json')
        self.original = self.path.read_bytes()

    def run_migration(self, **kwargs):
        return migrate(self.new, self.source.id, str(self.old), system='CURRENT', **kwargs)

    def test_move_preview_confirmation_preserves_archive_and_cost(self):
        preview = self.run_migration()
        self.assertEqual(preview['status'], 'preview')
        self.assertFalse((self.path.parent / (preview['new_session_id'] + '.json')).exists())
        result = self.run_migration(confirmation=preview['confirmation'])
        self.assertEqual(result['status'], 'migrated')
        store = SessionStore(self.new)
        try:
            migrated = store.load(result['new_session_id'])
        finally:
            store.close()
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertNotEqual(migrated.id, self.source.id)
        self.assertEqual(migrated.project, str(self.new))
        self.assertEqual(migrated.messages[0]['content'], 'CURRENT')
        self.assertEqual(migrated.messages[1:3], self.source.messages[1:3])
        self.assertEqual(migrated.billing['spent'], '0.20')
        self.assertNotIn('shared_request', migrated.billing)
        self.assertEqual(migrated.execution, self.source.execution)
        self.assertEqual(self.run_migration(confirmation=preview['confirmation'])['status'], 'already_migrated')

    def test_wrong_origin_stale_preview_and_same_directory_refused(self):
        with self.assertRaises(ValueError):
            migrate(self.new, self.source.id, str(self.base / 'wrong'), system='CURRENT')
        preview = self.run_migration()
        data = json.loads(self.path.read_text())
        data['title'] = 'modified after preview'
        self.path.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, 'snapshot'):
            self.run_migration(confirmation=preview['confirmation'])
        with self.assertRaises(ValueError):
            migrate(self.new, self.source.id, str(self.new), system='CURRENT')

    def test_pending_cost_action_or_inbox_never_migrates(self):
        for kind in ('cost', 'action', 'inbox'):
            with self.subTest(kind=kind):
                self.path.write_bytes(self.original)
                inbox = self.path.with_suffix('.inbox')
                inbox.unlink(missing_ok=True)
                data = json.loads(self.original)
                if kind == 'cost':
                    data['billing']['pending'] = data['billing']['request_cap']
                elif kind == 'action':
                    data['pending'] = {'reply': {}}
                else:
                    inbox.write_text('[{"lane":"followup","text":"run something"}]')
                self.path.write_text(json.dumps(data))
                with self.assertRaises(ValueError):
                    self.run_migration()
                self.assertEqual(len(list(self.path.parent.glob('*.json'))), 1)

    def test_active_source_lock_and_symlink_input_fail(self):
        store = SessionStore(self.new)
        store.acquire(self.source.id)
        try:
            with self.assertRaises((ValueError, BlockingIOError)):
                self.run_migration()
        finally:
            store.close()
        saved = self.path.with_suffix('.backup')
        self.path.rename(saved)
        self.path.symlink_to(saved)
        with self.assertRaises(ValueError):
            self.run_migration()

    def test_cli_migration_initializes_no_provider_or_configuration(self):
        from codine.cli import main
        output = io.StringIO()
        with patch('codine.cli.Config.load', side_effect=AssertionError('No configuration/provider init')), contextlib.redirect_stdout(output):
            code = main(['--project', str(self.new), '--migrate-session', self.source.id,
                         '--from-project', str(self.old), '--json'])
        self.assertEqual(code, 0)
        self.assertIn('"preview"', output.getvalue())
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_old_compatibility_symlink_does_not_redefine_origin(self):
        self.old.symlink_to(self.new, target_is_directory=True)
        preview = self.run_migration()
        self.assertEqual(preview['source_project'], str(self.old))
        self.assertEqual(self.run_migration(confirmation=preview['confirmation'])['status'], 'migrated')

    def test_malformed_billing_and_boolean_version_refused_without_archive_edit(self):
        for key, value in (('billing', []), ('billing', None), ('billing', ''), ('version', True)):
            with self.subTest(key=key, value=value):
                data = json.loads(self.original)
                data[key] = value
                self.path.write_text(json.dumps(data))
                snapshot = self.path.read_bytes()
                with self.assertRaises(ValueError):
                    self.run_migration()
                self.assertEqual(self.path.read_bytes(), snapshot)
                self.assertEqual(len(list(self.path.parent.glob('*.json'))), 1)
