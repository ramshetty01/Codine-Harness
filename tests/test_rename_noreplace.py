import os
from pathlib import Path
import tempfile
import unittest

from codine.rename_noreplace import rename_noreplace


class NoReplaceTests(unittest.TestCase):
    def test_new_empty_and_nonempty_destinations(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root);(path/'source').mkdir();(path/'source/owned').write_text('owned')
            fd=os.open(root,os.O_RDONLY|os.O_DIRECTORY)
            try:
                for destination in ('empty','nonempty'):
                    (path/destination).mkdir()
                    if destination=='nonempty':(path/destination/'existing').write_text('preserve')
                    with self.assertRaises(FileExistsError):
                        rename_noreplace('source',destination,src_dir_fd=fd,dst_dir_fd=fd)
                    self.assertEqual((path/'source/owned').read_text(),'owned')
                self.assertEqual((path/'nonempty/existing').read_text(),'preserve')
                rename_noreplace('source','new',src_dir_fd=fd,dst_dir_fd=fd)
                self.assertFalse((path/'source').exists())
                self.assertEqual((path/'new/owned').read_text(),'owned')
            finally:os.close(fd)
