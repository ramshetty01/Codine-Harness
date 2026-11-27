import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from codine.workspace import Workspace
from codine.repository_search import glob_match


class RepositorySearchTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.workspace = Workspace(self.root, lambda _: False, lambda *args: None)

    def file(self, name, content='needle\n'):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return path

    def test_name_and_segment_globs(self):
        for name in ('main.py', 'src/a.py', 'src/deep/b.py', 'src/a.js'):
            self.file(name)
        self.assertEqual(self.workspace.list_files(glob='*.js')['files'], ['src/a.js'])
        self.assertEqual(self.workspace.list_files(glob='src/*.py')['files'], ['src/a.py'])
        self.assertEqual(self.workspace.list_files(glob='**/*.py')['files'], ['main.py', 'src/a.py', 'src/deep/b.py'])
        self.assertFalse(glob_match('src/deep/b.py', 'src/?.py'))
        with self.assertRaises(ValueError): self.workspace.list_files(glob='../*')

    def test_nested_ignore_negation_and_narrow_scope_without_git_repo(self):
        self.file('.gitignore', '*.log\n!keep.log\ncache/\n')
        self.file('src/.gitignore', '*.tmp\n!keep.tmp\n')
        for name in ('drop.log', 'keep.log', 'cache/hidden.py', 'src/drop.tmp', 'src/keep.tmp', 'src/main.py'):
            self.file(name)
        files = self.workspace.list_files()['files']
        self.assertNotIn('drop.log', files)
        self.assertNotIn('cache/hidden.py', files)
        self.assertNotIn('src/drop.tmp', files)
        self.assertIn('keep.log', files)
        self.assertIn('src/keep.tmp', files)
        self.assertEqual(self.workspace.search('needle', path='src', glob='*.tmp')['matches'][0]['path'], 'src/keep.tmp')
        self.assertIn('drop.log', self.workspace.list_files(respect_ignore=False)['files'])

    def test_ignored_parent_cannot_be_reincluded_without_parent_rule(self):
        self.file('.gitignore', 'cache/\n!cache/keep.py\n')
        self.file('cache/keep.py')
        self.assertEqual(self.workspace.list_files(glob='*.py')['files'], [])

    def test_secret_links_never_appear_or_reach_search_worker(self):
        secret = self.file('.env', 'credential-canary')
        self.file('safe.py', 'needle')
        os.link(secret, self.root / 'alias.py')
        (self.root / 'linked.py').symlink_to(secret)
        (self.root / 'outside').symlink_to(self.root.parent, target_is_directory=True)
        listing = self.workspace.list_files(respect_ignore=False)
        self.assertEqual(listing['files'], ['safe.py'])
        result = self.workspace.search('credential', regex=True, respect_ignore=False)
        self.assertEqual(result['matches'], [])

    def test_regex_case_insensitive_and_malformed_pattern(self):
        self.file('a.py', 'Alpha 12\nalpha 99\nBeta 42\n')
        result = self.workspace.search(r'^alpha \d+$', regex=True, case_sensitive=False)
        self.assertEqual([row['line'] for row in result['matches']], [1, 2])
        self.assertEqual(len(self.workspace.search('ALPHA', case_sensitive=False)['matches']), 2)
        with self.assertRaisesRegex(ValueError, 'Invalid regex'):
            self.workspace.search('(', regex=True)

    def test_pathological_regex_hits_deadline_without_blocking_harness(self):
        self.file('a.txt', 'a' * 100000 + '!')
        started = time.monotonic()
        result = self.workspace.search('(a+)+$', regex=True)
        self.assertEqual(result['reason'], 'regex_timeout')
        self.assertTrue(result['truncated'])
        self.assertLess(time.monotonic() - started, 3.0)
        match = self.workspace.search('!', limit=1)['matches'][0]
        self.assertEqual(match['path'], 'a.txt')
        self.assertIn('!', match['text'])
        self.assertEqual(match['column'], 100001)

    def test_scan_file_match_and_byte_limits_are_honest(self):
        for number in range(10): self.file(f'{number}.txt', 'needle\n' * 20)
        with patch('codine.repository_search.MAX_ENTRIES', 4):
            result = self.workspace.list_files()
            self.assertTrue(result['truncated'])
            self.assertLessEqual(result['scanned_entries'], 5)
        with patch('codine.repository_search.MAX_CANDIDATES', 3):
            result = self.workspace.list_files()
            self.assertEqual(result['reason'], 'file_limit')
            self.assertEqual(len(result['files']), 3)
        self.assertEqual(self.workspace.search('needle', limit=2)['reason'], 'result_limit')
        with patch('codine.repository_search.MAX_BYTES', 150):
            result = self.workspace.search('absent')
            self.assertEqual(result['reason'], 'byte_limit')
            self.assertLessEqual(result['scanned_bytes'], 150)

    def test_ignore_worker_uses_isolated_metadata_and_no_project_helpers(self):
        self.file('.gitignore', '*.log\n')
        self.file('drop.log')
        self.file('safe.py')
        self.file('.git/config', '[core]\nfsmonitor = touch PWNED\n')
        result = self.workspace.list_files()
        self.assertNotIn('drop.log', result['files'])
        self.assertFalse((self.root / 'PWNED').exists())
        with patch('codine.repository_search.shutil.which', return_value=None):
            with self.assertRaisesRegex(ValueError, 'Git is required'):
                self.workspace.list_files()
        self.assertIn('safe.py', self.workspace.list_files(respect_ignore=False)['files'])
        with patch('codine.repository_search.shutil.which', return_value=str(self.root / 'git')):
            with self.assertRaisesRegex(ValueError, 'outside the model-editable project'):
                self.workspace.list_files()

    def test_directory_symlink_swap_cannot_redirect_scan(self):
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        Path(outside.name, 'private.txt').write_text('must never list')
        self.file('src/safe.py')
        original = os.open
        swapped = False
        def racing_open(path, flags, *args, **kwargs):
            nonlocal swapped
            if path == 'src' and not swapped:
                (self.root / 'src').rename(self.root / 'old-src')
                (self.root / 'src').symlink_to(outside.name, target_is_directory=True)
                swapped = True
            return original(path, flags, *args, **kwargs)
        with patch('codine.repository_search.os.open', side_effect=racing_open):
            result = self.workspace.list_files()
        self.assertTrue(swapped)
        self.assertFalse(any('private.txt' in name for name in result['files']))

    def test_hardlinked_or_oversized_ignore_fails_closed(self):
        secret = self.file('.env', '*.py\n')
        os.link(secret, self.root / '.gitignore')
        self.file('safe.py')
        with self.assertRaises(ValueError): self.workspace.list_files()
        (self.root / '.gitignore').unlink()
        self.file('.gitignore', '#' * 16385)
        with self.assertRaises(ValueError): self.workspace.list_files()
