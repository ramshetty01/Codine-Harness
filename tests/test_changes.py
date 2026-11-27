from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from codine.changes import ChangeStore
from codine.workspace import Workspace


class ChangesTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve()
        self.ws=Workspace(self.root,lambda _:True,lambda _:None,track_changes=True)
        self.store=ChangeStore(self.root)

    def test_edit_undo_survives_new_workspace_and_preserves_mode(self):
        path=self.root/'script.py'
        path.write_text('print(1)\n'); path.chmod(0o755)
        self.ws.read_file('script.py')
        self.ws.edit_file('script.py','1','2')
        self.ws=Workspace(self.root,lambda _:True,lambda _:None,track_changes=True)
        self.assertEqual(self.ws.undo()['status'],'undone')
        self.assertEqual(path.read_text(),'print(1)\n')
        self.assertEqual(path.stat().st_mode & 0o777,0o755)
        self.assertEqual(self.store.list(),[])

    def test_create_undo_does_not_delete_modified_file(self):
        self.ws.write_file('new.py','value = 1\n')
        (self.root/'new.py').write_text('external edit\n')
        with self.assertRaisesRegex(ValueError,'changed'): self.ws.undo()
        self.assertEqual((self.root/'new.py').read_text(),'external edit\n')

    def test_create_undo_removes_only_original_created_file(self):
        self.ws.write_file('new.py','value = 1\n')
        self.ws.undo()
        self.assertFalse((self.root/'new.py').exists())

    def test_declined_and_planning_undo_preserve_contents(self):
        self.ws.write_file('new.py','value = 1\n')
        self.ws.approve=lambda _:False
        self.assertEqual(self.ws.undo()['status'],'declined')
        self.ws.mode='plan'
        with self.assertRaises(ValueError): self.ws.undo()
        self.assertTrue((self.root/'new.py').exists())

    def test_edit_during_undo_approval_is_rejected(self):
        self.ws.write_file('new.py','value = 1\n')
        def approve(_):
            (self.root/'new.py').write_text('other work\n')
            return True
        self.ws.approve=approve
        with self.assertRaisesRegex(ValueError,'changed'): self.ws.undo()
        self.assertEqual((self.root/'new.py').read_text(),'other work\n')

    def test_checkpoint_failure_prevents_edit(self):
        path=self.root/'file.py'; path.write_text('value = 1\n')
        self.ws.read_file('file.py')
        with patch.object(ChangeStore,'save',side_effect=OSError('disk full')):
            with self.assertRaises(OSError): self.ws.edit_file('file.py','1','2')
        self.assertEqual(path.read_text(),'value = 1\n')

    def test_no_checkpoint_side_effects_at_construction(self):
        self.assertFalse((self.root/'.codine').exists())

    def test_two_changes_undo_in_reverse_order(self):
        self.ws.write_file('file.py','value = 1\n')
        self.ws.edit_file('file.py','1','2')
        self.ws.undo()
        self.assertEqual((self.root/'file.py').read_text(),'value = 1\n')
        self.ws.undo()
        self.assertFalse((self.root/'file.py').exists())

    def test_diff_shows_newer_work_without_changing_it(self):
        self.ws.write_file('file.py','value = 1\n')
        (self.root/'file.py').write_text('value = 2\n')
        self.assertIn('+value = 2',self.ws.show_change())
        self.assertEqual((self.root/'file.py').read_text(),'value = 2\n')
