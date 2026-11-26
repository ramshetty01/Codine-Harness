import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from codine.sandbox import DockerSandbox, SandboxError, snapshot_files, validate_files

class StagingTests(unittest.TestCase):
    def test_rejects_unsafe_paths_and_limits(self):
        for name in ('../task.py', '/task.py', './task.py', 'pkg//task.py', '.env', '.git/a.py', 'venv/a.py', 'a\\b.py'):
            with self.subTest(name=name), self.assertRaises(SandboxError):
                validate_files({name:''}, name)
        with self.assertRaises(SandboxError):
            validate_files({'task.py':'x'*200001}, 'task.py')
        with self.assertRaises(SandboxError):
            validate_files({'task.py':''}, 'missing.py')

    def test_only_manifest_files_are_selected_and_symlinks_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'task.py').write_text('def solve(): return 1')
            (root/'.env').write_text('SECRET=not-for-staging')
            self.assertEqual(set(snapshot_files(root,['task.py'],'task.py')), {'task.py'})
            (root/'alias.py').symlink_to(root/'task.py')
            with self.assertRaises(SandboxError):
                snapshot_files(root,['alias.py'],'alias.py')
            (root/'pkg').symlink_to(root, target_is_directory=True)
            with self.assertRaises(SandboxError):
                snapshot_files(root,['pkg/task.py'],'pkg/task.py')

    def test_empty_split_is_not_reported_as_success(self):
        from codine.evals import main
        with patch('codine.sandbox.DockerSandbox'), patch('sys.stderr'), self.assertRaises(SystemExit) as error:
            main(['--offline','--executor','docker','--suite','repository','--split','holdout'])
        self.assertEqual(error.exception.code,2)

    def test_repository_suite_refuses_local_execution(self):
        from codine.evals import main
        with patch('sys.stderr'), self.assertRaises(SystemExit) as error:
            main(['--offline','--suite','repository'])
        self.assertEqual(error.exception.code,2)

@unittest.skipUnless(os.getenv('CODINE_DOCKER_TESTS')=='1','Requires Docker opt-in')
class RepositoryDockerTests(unittest.TestCase):
    def test_package_import_and_missing_dependency(self):
        sandbox=DockerSandbox()
        files={'task.py':'from pkg.helper import double\ndef solve(x): return double(x)\n',
               'pkg/__init__.py':'', 'pkg/helper.py':'def double(x): return x*2\n'}
        self.assertEqual(sandbox.evaluate_files(files,'task.py',[[3]])['values'],[6])
        del files['pkg/helper.py']
        self.assertEqual(sandbox.evaluate_files(files,'task.py',[[3]])['status'],'candidate_error')

    def test_repository_tasks_run_through_agent(self):
        import json
        from codine.config import APP_ROOT
        from codine.evals import run_case
        sandbox=DockerSandbox()
        for task in json.loads((APP_ROOT/'evals/repository_tasks.json').read_text())['tasks']:
            with self.subTest(task=task['id']):
                result=run_case(task,sandbox)
                self.assertTrue(result['passed'],result)
                self.assertTrue(result['broken_fixture_rejected'])
