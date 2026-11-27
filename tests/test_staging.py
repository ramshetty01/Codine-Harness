import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from codine.staging import snapshot_repository, StagingError, StagingLimits
from codine.sandbox import snapshot_files, SandboxError


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'src').mkdir()
        (self.root / 'src/main.py').write_text('print("hello")\n')
        (self.root / 'pyproject.toml').write_text('[project]\nname="sample"\n')
        (self.root / '.env').write_text('SECRET=excluded')

    def test_explicit_selection_is_immutable_sorted_and_content_addressed(self):
        paths = ['src/main.py', 'pyproject.toml']
        first = snapshot_repository(self.root, paths)
        second = snapshot_repository(self.root, reversed(paths))
        self.assertEqual(first.digest, second.digest)
        self.assertEqual(set(first.as_text_files()), set(paths))
        copied = first.as_text_files()
        copied.clear()
        self.assertEqual(len(first.files), 2)
        (self.root / 'src/main.py').write_text('changed')
        self.assertNotEqual(first.digest, snapshot_repository(self.root, paths).digest)
        self.assertEqual(first.files[1].content, 'print("hello")\n')

    def test_path_policy_rejects_before_reading(self):
        for path in ('../x.py', '/x.py', './x.py', 'src//x.py', 'src/../x.py',
                     'a\\b.py', 'C:x.py', 'x\0.py', '.env', '.env.example',
                     '.git/config', '.codine/session.json', 'dist/x.js',
                     'node_modules/x.js', '.aws/config', 'secrets.json', 'secrets.toml',
                     'credentials.ini', 'target/source.rs', 'pkg.egg-info/data.txt',
                     'src/token.key', 'unknown.bin', 'a\nx.py'):
            with self.subTest(path=path), self.assertRaises(StagingError):
                snapshot_repository(self.root, [path])

    def test_counts_duplicates_and_byte_limits(self):
        for paths in ([], ['src/main.py'] * 2, ['src/main.py', 'SRC/main.py']):
            with self.subTest(paths=paths), self.assertRaises(StagingError):
                snapshot_repository(self.root, paths)
        with self.assertRaises(StagingError):
            snapshot_repository(self.root, ['src/main.py', 'pyproject.toml'], StagingLimits(max_files=1))
        with self.assertRaises(StagingError):
            snapshot_repository(self.root, ['src/main.py'], StagingLimits(max_file_bytes=1))
        with self.assertRaises(StagingError):
            snapshot_repository(self.root, ['src/main.py', 'pyproject.toml'], StagingLimits(max_total_bytes=25))
        (self.root/'unicode.txt').write_text('é')
        self.assertEqual(snapshot_repository(self.root,['unicode.txt']).total_bytes,2)
        with self.assertRaises(StagingError):
            snapshot_repository(self.root,['unicode.txt'],StagingLimits(max_file_bytes=1))

    def test_symlink_hardlink_special_and_directory_refused(self):
        (self.root/'alias.py').symlink_to(self.root/'src/main.py')
        (self.root/'linked').symlink_to(self.root/'src', target_is_directory=True)
        os.link(self.root/'src/main.py', self.root/'hard.py')
        os.mkfifo(self.root/'pipe.py')
        (self.root/'dir.py').mkdir()
        for path in ('alias.py', 'linked/main.py', 'hard.py', 'src/main.py', 'pipe.py', 'dir.py'):
            with self.subTest(path=path), self.assertRaises(StagingError):
                snapshot_repository(self.root,[path])
        with self.assertRaises(SandboxError):
            snapshot_files(self.root,['hard.py'],'hard.py')

    def test_binary_invalid_utf8_and_missing_refused(self):
        for content in (b'\0', b'\xff'):
            (self.root/'data.txt').write_bytes(content)
            with self.assertRaises(StagingError):
                snapshot_repository(self.root,['data.txt'])
        with self.assertRaises(StagingError):
            snapshot_repository(self.root,['missing.py'])

    def test_change_during_read_fails_closed(self):
        original = os.read
        def changing_read(fd, count):
            data = original(fd, count)
            if data:
                (self.root/'src/main.py').write_text('replacement contents longer')
            return data
        with patch('codine.staging.os.read', side_effect=changing_read), self.assertRaises(StagingError):
            snapshot_repository(self.root,['src/main.py'])

    def test_parent_symlink_swap_cannot_redirect_open_descriptor(self):
        original = os.open
        switched = False
        def swapping_open(path, flags, *args, **kwargs):
            nonlocal switched
            fd = original(path, flags, *args, **kwargs)
            if path == 'src' and not switched:
                switched = True
                (self.root/'src').rename(self.root/'old')
                (self.root/'src').symlink_to('/tmp',target_is_directory=True)
            return fd
        with patch('codine.staging.os.open', side_effect=swapping_open):
            # Preserve the capability check while replacing the callable.
            with patch('codine.staging.os.supports_dir_fd', {os.open}):
                manifest = snapshot_repository(self.root,['src/main.py'])
        self.assertEqual(manifest.files[0].content,'print("hello")\n')
