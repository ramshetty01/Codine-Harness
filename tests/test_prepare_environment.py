import io
import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest

from codine.prepare_environment import CLEAN_ENTRYPOINT, EnvironmentPreparer, PreparationError, archive, bounded_run, selected_files

BASE = 'sha256:' + 'a' * 64
BUILT = 'sha256:' + 'b' * 64


class FakeDocker:
    def __init__(self):
        self.calls = []
        self.key = None
        self.exit_code = 0
        self.fail_start = False
        self.env = ['PATH=/usr/bin']
        self.bad_label = False
        self.architecture = 'arm64'

    def __call__(self, args, **kwargs):
        self.calls.append((args, kwargs))
        op = args[1]
        if op == 'image' and args[2] == 'inspect':
            return json.dumps({'Id': args[-1], 'Os': 'linux', 'Architecture': self.architecture, 'Config': {'Env': self.env, 'Entrypoint': CLEAN_ENTRYPOINT, 'Cmd': ['python3'], 'User': '65534:65534', 'Labels':
                {'codine.environment.key': 'wrong' if self.bad_label else self.key}}})
        if op == 'start' and self.fail_start:
            raise PreparationError('simulated timeout')
        if op == 'inspect':
            return json.dumps({'Running': False, 'ExitCode': self.exit_code, 'OOMKilled': False})
        if op == 'commit':
            self.key = next(arg.split('=', 1)[1] for arg in args if arg.startswith('LABEL codine.environment.key='))
            return BUILT
        return ''


class PreparationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'requirements.lock').write_text('# explicit empty stdlib environment\n')
        self.fake = FakeDocker()
        self.preparer = EnvironmentPreparer(self.root / 'cache', self.fake, 'docker')

    def prepare(self, **kwargs):
        return self.preparer.prepare(self.root, ['requirements.lock'], ['python', '-V'], BASE, **kwargs)

    def test_default_is_bounded_offline_no_mounts_and_clean_argv(self):
        result = self.prepare()
        create = next(args for args, _ in self.fake.calls if args[1] == 'create')
        self.assertEqual(create[create.index('--network')+1], 'none')
        self.assertIn('--memory', create)
        self.assertIn('--cap-drop', create)
        self.assertNotIn('--volume', create)
        self.assertEqual(create[create.index('--entrypoint')+1], '/usr/bin/env')
        self.assertIn('-i', create)
        self.assertEqual(result['image'], BUILT)
        self.assertEqual(self.fake.calls[-1][0][1:3], ['rm', '--force'])
        receipt = (self.root / 'cache' / (result['key'] + '.json')).read_text()
        self.assertNotIn('python', receipt)  # argv is hashed, not persisted
        commit = next(args for args, _ in self.fake.calls if args[1] == 'commit')
        self.assertIn('ENTRYPOINT ' + json.dumps(CLEAN_ENTRYPOINT), commit)
        self.assertIn('USER 65534:65534', commit)

    def test_network_is_explicit_and_part_of_cache_key(self):
        first = self.prepare()
        second = self.prepare(allow_network=True)
        self.assertNotEqual(first['key'], second['key'])
        create = [args for args, _ in self.fake.calls if args[1]=='create'][-1]
        self.assertEqual(create[create.index('--network')+1], 'bridge')

    def test_cache_requires_manifest_and_image_label_match(self):
        first = self.prepare()
        self.assertTrue(self.prepare()['cached'])
        (self.root / 'requirements.lock').write_text('# changed\n')
        changed = self.prepare()
        self.assertNotEqual(first['key'], changed['key'])
        self.fake.bad_label = True
        with self.assertRaisesRegex(PreparationError, 'label'):
            self.prepare()
        self.assertEqual(len([args for args, _ in self.fake.calls if args[1]=='create']), 3)

    def test_same_multiarch_image_id_does_not_share_cache_between_platforms(self):
        first = self.prepare()
        self.fake.architecture = 'amd64'
        second = self.prepare()
        self.assertNotEqual(first['key'], second['key'])
        self.assertFalse(second['cached'])

    def test_failures_clean_up_without_committing(self):
        for timeout in (False, True):
            self.fake = FakeDocker()
            self.preparer.run = self.fake
            self.fake.exit_code = 1
            self.fake.fail_start = timeout
            with self.assertRaises(PreparationError):
                self.prepare()
            self.assertEqual(self.fake.calls[-1][0][1], 'rm')
            self.assertFalse(any(args[1]=='commit' for args, _ in self.fake.calls))

    def test_rejects_mutable_base_credentials_symlinks_and_escape(self):
        with self.assertRaises(PreparationError):
            self.preparer.prepare(self.root, ['requirements.lock'], ['python'], 'python:latest')
        self.fake.env = ['API_KEY=not-forwarded']
        with self.assertRaises(PreparationError):
            self.prepare()
        for name in ('../requirements.lock', '.env', '/requirements.lock'):
            with self.assertRaises(PreparationError):
                selected_files(self.root, [name])
        (self.root / 'requirements.txt').symlink_to(self.root / 'requirements.lock')
        with self.assertRaises(PreparationError):
            selected_files(self.root, ['requirements.txt'])
        (self.root / 'requirements.lock').write_text('https://user:password@example.invalid/package')
        with self.assertRaises(PreparationError):
            selected_files(self.root, ['requirements.lock'])

    def test_archive_contains_only_selected_regular_files(self):
        data = archive(selected_files(self.root, ['requirements.lock']))
        with tarfile.open(fileobj=io.BytesIO(data)) as tar:
            self.assertEqual(tar.getnames(), ['codine-setup/requirements.lock'])
            self.assertTrue(all(item.isfile() for item in tar))

    def test_subprocess_output_and_timeout_are_bounded(self):
        self.assertEqual(bounded_run([sys.executable, '-c', 'print("ok")']), 'ok')
        with self.assertRaisesRegex(PreparationError, 'output'):
            bounded_run([sys.executable, '-c', 'print("x" * 100000)'], limit=1024)
        with self.assertRaisesRegex(PreparationError, 'timed out'):
            bounded_run([sys.executable, '-c', 'import time; time.sleep(5)'], timeout=0.1)


@unittest.skipUnless(os.getenv('CODINE_DOCKER_TESTS') == '1', 'Requires Docker opt-in and local Python image')
class RealPreparationTests(unittest.TestCase):
    def test_prepared_dependency_is_readable_in_offline_nonroot_executor(self):
        from codine.sandbox import DockerSandbox
        base = DockerSandbox().image
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'requirements.lock').write_text('# stdlib-only preparation test\n')
            preparer = EnvironmentPreparer(root / 'cache')
            command = ['python', '-c',
                'import sysconfig; from pathlib import Path; '
                'Path(sysconfig.get_paths()["purelib"], "codine_prepared_probe.py").write_text("VALUE = 42\\n")']
            result = preparer.prepare(root, ['requirements.lock'], command, base)
            try:
                self.assertTrue(preparer.prepare(root, ['requirements.lock'], command, base)['cached'])
                executor = DockerSandbox(result['image'])
                value = executor.evaluate('import codine_prepared_probe\ndef solve(): return codine_prepared_probe.VALUE', [[]])
                self.assertEqual(value['values'], [42])
            finally:
                preparer.control(['image', 'rm', result['image']])
