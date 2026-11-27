import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from codine.repository_executor import RepositoryExecutor
from codine.sandbox import SandboxError
from codine.staging import Manifest, StagedFile, StagingError, snapshot_repository


class CommandPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root/'task.py').write_text('print("ok")')
        self.manifest = snapshot_repository(self.root,['task.py'])
        self.executor = object.__new__(RepositoryExecutor)

    def test_invalid_command_cwd_environment_fail_before_transport(self):
        cases = [([],{}), ('python task.py',{}), (['python'],{'cwd':'../'}),
                 (['python'],{'cwd':'/tmp'}), (['python'],{'cwd':'missing'}),
                 (['python'],{'env':{'OPENROUTER_API_KEY':'secret'}}),
                 (['python'],{'env':{'PATH':'/host/bin'}}), (['a\0b'],{})]
        with patch.object(self.executor,'_run_payload') as run:
            for argv,kwargs in cases:
                with self.subTest(argv=argv,kwargs=kwargs), self.assertRaises(SandboxError):
                    self.executor.execute(self.manifest,argv,**kwargs)
            run.assert_not_called()

    def test_forged_manifest_rejected(self):
        for entry in (StagedFile('../task.py','x','bad',1),
                      StagedFile('task.py','x','bad',1)):
            with self.assertRaises((SandboxError,StagingError)):
                self.executor.execute(Manifest((entry,)),['python'])


@unittest.skipUnless(os.getenv('CODINE_DOCKER_TESTS') == '1','Requires Docker opt-in')
class RepositoryCommandDockerTests(unittest.TestCase):
    def run_command(self,source,argv=None,**kwargs):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'tests').mkdir()
            (root/'tests/test_app.py').write_text(source)
            manifest=snapshot_repository(root,['tests/test_app.py'])
            executor=RepositoryExecutor(timeout=kwargs.pop('timeout',5),output_limit=2048)
            return executor.execute(manifest,argv or ['python','tests/test_app.py'],**kwargs)

    def test_actual_unittest_success_and_failure_exit_status(self):
        source='import unittest\nclass Test(unittest.TestCase):\n def test_value(self): self.assertEqual(2, EXPECTED)\n'
        for expected,code in ((2,0),(3,1)):
            result=self.run_command(source.replace('EXPECTED',str(expected)),
                ['python','-m','unittest','discover','-s','tests','-v'])
            self.assertEqual(result['status'],'completed',result)
            self.assertEqual(result['exit_code'],code,result)
            self.assertIn('Ran 1 test',result['stderr'])
            self.assertEqual(len(result['manifest_digest']),64)

    def test_cwd_explicit_env_and_no_host_secret_or_mount(self):
        source='''import os, pathlib, sys
assert os.getcwd() == '/tmp/project/tests'
assert os.environ.get('CI') == '1'
assert 'CODINE_TEST_SECRET' not in os.environ
assert not pathlib.Path('/Users/ramshetty').exists()
assert os.getuid() == 65534
print('stdout marker')
print('stderr marker',file=sys.stderr)
'''
        with patch.dict(os.environ,{'CODINE_TEST_SECRET':'must-not-leak'}):
            result=self.run_command(source,['python','test_app.py'],cwd='tests',env={'CI':'1'})
        self.assertEqual(result['exit_code'],0,result)
        self.assertEqual(result['stdout'],'stdout marker\n')
        self.assertEqual(result['stderr'],'stderr marker\n')

    def test_network_and_root_writes_blocked(self):
        result=self.run_command('''import pathlib, socket
try:
 pathlib.Path('/blocked').write_text('bad')
except OSError: pass
else: raise AssertionError('root writable')
s=socket.socket(); s.settimeout(.2)
try: s.connect(('1.1.1.1',443))
except OSError: pass
else: raise AssertionError('network available')
print('isolated')
''')
        self.assertEqual(result['exit_code'],0,result)

    def test_timeout_output_limit_and_cleanup(self):
        for source,expected in [('import time; time.sleep(60)','timeout'),
                                ('while True: print("x"*4096,flush=True)','output_limit')]:
            with self.subTest(expected=expected):
                result=self.run_command(source,timeout=2)
                self.assertEqual(result['status'],expected,result)
                self.assertLessEqual(len(result['stdout'].encode())+len(result['stderr'].encode()),2048)
        # Every executor rm is exercised above; additionally confirm no containers
        # with the exact names created by this test survive.

    def test_cleanup_targets_the_created_container(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); (root/'task.py').write_text('import time; time.sleep(60)')
            executor=RepositoryExecutor(timeout=1)
            calls=[]
            control=executor._control
            def tracking(args):
                calls.append(args)
                return control(args)
            with patch.object(executor,'_control',side_effect=tracking):
                result=executor.execute(snapshot_repository(root,['task.py']),['python','task.py'])
            self.assertEqual(result['status'],'timeout',result)
            name=next(args[args.index('--name')+1] for args in calls if args[0]=='create')
            self.assertIn(['rm','--force',name],calls)
            with self.assertRaises(SandboxError):
                control(['inspect',name])
