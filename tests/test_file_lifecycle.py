import os
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from codine.changes import ChangeStore
from codine.command_policy import CommandPolicy, FileRule
from codine.workspace import Workspace


class FileLifecycleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.preview = []
        self.workspace = Workspace(self.root, Mock(return_value=True), self.preview.append, app_root=self.root)
        self.data = b'\x00\xffbinary data\x01'
        (self.root / 'source.bin').write_bytes(self.data)
        self.meta = self.workspace.inspect_file('source.bin')

    def move(self):
        return self.workspace.move_file('source.bin', 'nested/destination.bin', self.meta['sha256'])

    def test_binary_move_delete_recovery_and_modes_without_fake_text_diff(self):
        os.chmod(self.root / 'source.bin', 0o751)
        self.meta = self.workspace.inspect_file('source.bin')
        result = self.move()
        self.assertFalse((self.root / 'source.bin').exists())
        self.assertEqual((self.root / 'nested/destination.bin').read_bytes(), self.data)
        self.assertEqual((self.root / 'nested/destination.bin').stat().st_mode & 0o777, 0o751)
        self.assertTrue(any('binary' in line and self.meta['sha256'] in line for line in self.preview))
        self.assertFalse(any(line.startswith('---') for line in self.preview))
        record = ChangeStore(self.root).load(result['change_id'])
        self.assertEqual(record['record_version'], 2)
        self.assertIn('before_b64', record)
        self.workspace.undo(result['change_id'])
        self.assertEqual((self.root / 'source.bin').read_bytes(), self.data)
        self.meta = self.workspace.inspect_file('source.bin')
        deleted = self.workspace.delete_file('source.bin', self.meta['sha256'])
        self.assertFalse((self.root / 'source.bin').exists())
        self.workspace.undo(deleted['change_id'])
        self.assertEqual((self.root / 'source.bin').read_bytes(), self.data)

    def test_metadata_inspection_does_not_grant_text_edit_coverage(self):
        (self.root / 'a.py').write_text('print(1)\n')
        self.workspace.inspect_file('a.py')
        with self.assertRaises(ValueError): self.workspace.edit_file('a.py', '1', '2')
        self.assertEqual((self.root / 'a.py').read_text(), 'print(1)\n')

    def test_complete_text_read_can_establish_rename_version(self):
        (self.root / 'a.py').write_text('print(1)\n')
        version = self.workspace.read_file('a.py')['sha256']
        result = self.workspace.rename_file('a.py', 'b.py', version)
        self.assertEqual(result['status'], 'moved')
        self.assertEqual((self.root / 'b.py').read_text(), 'print(1)\n')

    def test_uninspected_stale_links_directories_and_excluded_paths_rejected(self):
        (self.root / 'new.bin').write_bytes(self.data)
        with self.assertRaises(ValueError): self.workspace.delete_file('new.bin', self.meta['sha256'])
        (self.root / 'source.bin').write_bytes(b'changed')
        with self.assertRaises(ValueError): self.move()
        os.link(self.root / 'new.bin', self.root / 'hard.bin')
        (self.root / 'link.bin').symlink_to(self.root / 'new.bin')
        (self.root / 'directory').mkdir()
        for name in ('hard.bin', 'link.bin', 'directory', '.env', '../outside'):
            with self.subTest(name=name), self.assertRaises((ValueError, OSError)):
                self.workspace.inspect_file(name)

    def test_both_approvals_precede_mutation_and_core_cannot_auto_allow(self):
        self.workspace.approve.side_effect = [True, False]
        self.assertEqual(self.move()['status'], 'declined')
        self.assertFalse((self.root / 'nested').exists())
        self.assertTrue((self.root / 'source.bin').exists())
        self.workspace.mode = 'auto-edit'
        self.workspace.approve.side_effect = None
        self.workspace.approve.return_value = False
        self.assertEqual(self.workspace.move_file('source.bin', 'main.py', self.meta['sha256'])['status'], 'declined')
        self.assertTrue((self.root / 'source.bin').exists())

    def test_source_or_destination_changes_during_approval_do_not_overwrite(self):
        def changed_source(_):
            (self.root / 'source.bin').write_bytes(b'new user work')
            return True
        self.workspace.approve.side_effect = changed_source
        with self.assertRaises(ValueError): self.move()
        self.assertEqual((self.root / 'source.bin').read_bytes(), b'new user work')
        self.assertFalse((self.root / 'nested').exists())
        (self.root / 'source.bin').write_bytes(self.data)
        self.meta = self.workspace.inspect_file('source.bin')
        def appeared(_):
            (self.root / 'nested').mkdir(exist_ok=True)
            (self.root / 'nested/destination.bin').write_bytes(b'new destination')
            return True
        self.workspace.approve.side_effect = appeared
        with self.assertRaises(ValueError): self.move()
        self.assertEqual((self.root / 'nested/destination.bin').read_bytes(), b'new destination')
        self.assertEqual((self.root / 'source.bin').read_bytes(), self.data)

    def test_copy_interruption_before_source_unlink_is_recoverable(self):
        with patch('codine.file_lifecycle.remove_guarded', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt): self.move()
        self.assertEqual((self.root / 'source.bin').read_bytes(), self.data)
        self.assertEqual((self.root / 'nested/destination.bin').read_bytes(), self.data)
        record = ChangeStore(self.root).list(1)[0]
        self.assertEqual(record['phase'], 'copied')
        self.workspace.undo(record['id'])
        self.assertEqual((self.root / 'source.bin').read_bytes(), self.data)
        self.assertFalse((self.root / 'nested/destination.bin').exists())

    def partial_write(self):
        original, calls = os.write, 0
        def write(fd, data):
            nonlocal calls
            calls += 1
            if calls == 1: return original(fd, data[:2])
            raise OSError('disk failed mid-copy')
        return write

    def test_partial_forward_and_reverse_copy_recover_without_losing_original(self):
        with patch('codine.file_lifecycle.os.write', side_effect=self.partial_write()):
            with self.assertRaises(OSError): self.move()
        self.assertEqual((self.root / 'nested/destination.bin').read_bytes(), self.data[:2])
        record = ChangeStore(self.root).list(1)[0]
        self.workspace.undo(record['id'])
        self.assertEqual((self.root / 'source.bin').read_bytes(), self.data)
        moved = self.move()
        with patch('codine.file_lifecycle.os.write', side_effect=self.partial_write()):
            with self.assertRaises(OSError): self.workspace.undo(moved['change_id'])
        self.assertEqual((self.root / 'source.bin').read_bytes(), self.data[:2])
        self.workspace.undo(moved['change_id'])
        self.assertEqual((self.root / 'source.bin').read_bytes(), self.data)
        self.assertFalse((self.root / 'nested/destination.bin').exists())

    def test_checkpoint_failure_before_action_preserves_source(self):
        with patch.object(ChangeStore, 'save', side_effect=OSError('cannot checkpoint')):
            with self.assertRaises(OSError): self.move()
        self.assertEqual((self.root / 'source.bin').read_bytes(), self.data)
        self.assertFalse((self.root / 'nested').exists())

    def test_completion_record_failure_still_allows_delete_recovery(self):
        save = ChangeStore.save
        def fail_completed(store, record):
            if record.get('phase') == 'completed': raise OSError('late journal failure')
            return save(store, record)
        with patch.object(ChangeStore, 'save', fail_completed):
            with self.assertRaises(OSError): self.workspace.delete_file('source.bin', self.meta['sha256'])
        self.assertFalse((self.root / 'source.bin').exists())
        self.workspace.undo(ChangeStore(self.root).list(1)[0]['id'])
        self.assertEqual((self.root / 'source.bin').read_bytes(), self.data)

    def test_recovery_refuses_newer_destination_and_source_work(self):
        result = self.move()
        (self.root / 'nested/destination.bin').write_bytes(b'user edits')
        with self.assertRaises(ValueError): self.workspace.undo(result['change_id'])
        self.assertEqual((self.root / 'nested/destination.bin').read_bytes(), b'user edits')
        self.assertFalse((self.root / 'source.bin').exists())

    def test_oversized_special_files_and_corrupted_before_images_fail_closed(self):
        (self.root / 'large.bin').write_bytes(b'x' * 2000001)
        os.mkfifo(self.root / 'pipe')
        for name in ('large.bin', 'pipe'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.workspace.inspect_file(name)
        result = self.workspace.delete_file('source.bin', self.meta['sha256'])
        store = ChangeStore(self.root)
        path = store._path(result['change_id'])
        record = json.loads(path.read_text())
        record['before_b64'] = '!corrupt!'
        path.write_text(json.dumps(record))
        with self.assertRaises(ValueError): self.workspace.undo(result['change_id'])
        self.assertFalse((self.root / 'source.bin').exists())

    def test_source_destination_policy_and_read_policy_apply(self):
        for operation, name in (('write', 'source.bin'), ('write', 'nested/destination.bin'), ('read', 'source.bin')):
            rule = FileRule(id='deny', action='deny', operation=operation, project=str(self.root), match='exact', path=name)
            self.workspace.command_policy = CommandPolicy(self.root / 'policy.json', file_rules=(rule,))
            self.workspace.policy_interactive = True
            with self.assertRaisesRegex(ValueError, 'DENY'): self.move()
            self.assertTrue((self.root / 'source.bin').exists())
            self.assertFalse((self.root / 'nested').exists())
