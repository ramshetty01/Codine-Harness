from pathlib import Path
import tempfile
import unittest
from codine.workspace import Workspace

class LineEditTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.path=self.root/'file.py'
        self.path.write_text('x = 1\nx = 1\nprint(x)\n')
        self.ws=Workspace(self.root,lambda _:True,lambda _:None)

    def test_replaces_only_selected_duplicate_line(self):
        read=self.ws.read_file('file.py')
        self.ws.replace_lines('file.py',2,2,'x = 2\n',read['sha256'])
        self.assertEqual(self.path.read_text(),'x = 1\nx = 2\nprint(x)\n')

    def test_sha_alone_does_not_authorize_unread_region(self):
        read=self.ws.read_file('file.py',max_lines=1)
        with self.assertRaisesRegex(ValueError,'complete line range'):
            self.ws.replace_lines('file.py',2,2,'x = 2\n',read['sha256'])
        self.assertEqual(self.path.read_text(),'x = 1\nx = 1\nprint(x)\n')

    def test_stale_sha_and_newer_work_are_rejected(self):
        read=self.ws.read_file('file.py')
        self.path.write_text('new work\n')
        with self.assertRaisesRegex(ValueError,'changed'):
            self.ws.replace_lines('file.py',1,1,'bad\n',read['sha256'])
        self.assertEqual(self.path.read_text(),'new work\n')

    def test_partial_long_line_cannot_be_replaced(self):
        self.path.write_text('x'*25000+'\n')
        read=self.ws.read_file('file.py')
        with self.assertRaisesRegex(ValueError,'complete line range'):
            self.ws.replace_lines('file.py',1,1,'short\n',read['sha256'])

    def test_decline_and_line_join_are_safe(self):
        read=self.ws.read_file('file.py')
        with self.assertRaisesRegex(ValueError,'newline'):
            self.ws.replace_lines('file.py',1,1,'x = 2',read['sha256'])
        self.ws.approve=lambda _:False
        self.assertEqual(self.ws.replace_lines('file.py',1,1,'x = 2\n',read['sha256'])['status'],'declined')
        self.assertTrue(self.path.read_text().startswith('x = 1'))

    def test_search_accepts_single_file(self):
        result=self.ws.search('print',path='file.py')
        self.assertEqual(result['matches'][0]['line'],3)

    def test_line_edit_is_checkpointed_and_undoable(self):
        self.ws.track_changes=True
        before=self.path.read_text()
        read=self.ws.read_file('file.py')
        self.ws.replace_lines('file.py',2,2,'x = 2\n',read['sha256'])
        self.ws.undo()
        self.assertEqual(self.path.read_text(),before)
