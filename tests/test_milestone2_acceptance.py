"""The build-artifact portion of the sandbox milestone gate; no API calls."""
import os
from pathlib import Path
import tempfile
import unittest

from codine.repository_executor import RepositoryExecutor
from codine.staging import snapshot_repository


@unittest.skipUnless(os.getenv('CODINE_DOCKER_TESTS') == '1', 'Requires Docker opt-in and local Python image')
class SandboxBuildAcceptanceTests(unittest.TestCase):
    def test_build_python_archive_and_run_its_smoke_test_inside_container(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'app').mkdir()
            (root/'app/__main__.py').write_text('print(6 * 7)\n')
            (root/'build_verify.py').write_text('''import pathlib, subprocess, sys, zipfile
subprocess.run([sys.executable, '-m', 'zipapp', 'app', '-o', 'app.pyz'], check=True)
assert zipfile.is_zipfile('app.pyz'), 'build did not produce a Python archive'
completed = subprocess.run([sys.executable, 'app.pyz'], check=True, text=True, capture_output=True)
assert completed.stdout == '42\\n', completed.stdout
print('BUILD_AND_SMOKE_PASS')
''')
            manifest=snapshot_repository(root,['app/__main__.py','build_verify.py'])
            result=RepositoryExecutor(timeout=10).execute(manifest,['python','build_verify.py'])
            self.assertEqual(result['status'],'completed',result)
            self.assertEqual(result['exit_code'],0,result)
            self.assertEqual(result['stdout'],'BUILD_AND_SMOKE_PASS\n')
            self.assertFalse((root/'app.pyz').exists(), 'container artifact escaped to host')
            self.assertEqual((root/'app/__main__.py').read_text(),'print(6 * 7)\n')
