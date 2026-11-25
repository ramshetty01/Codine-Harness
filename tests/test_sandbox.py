"""Policy tests plus opt-in Docker boundary checks (no model/API requests)."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from codine.sandbox import DockerSandbox, SandboxError


class SandboxPolicyTests(unittest.TestCase):
    def test_policy_has_no_host_mounts_or_network(self):
        sandbox=object.__new__(DockerSandbox)
        sandbox.image='sha256:'+'a'*64
        args=sandbox.create_args('codine-eval-test')
        for token in ('--read-only','--cap-drop','--pids-limit','--memory','--cpus','--user','--pull'):
            self.assertIn(token,args)
        self.assertEqual(args[args.index('--network')+1],'none')
        self.assertEqual(args[args.index('--user')+1],'65534:65534')
        self.assertNotIn('--volume',args)
        self.assertNotIn('--mount',args)
        self.assertNotIn('--privileged',args)

    def test_missing_docker_fails_closed(self):
        with patch('codine.sandbox.shutil.which',return_value=None), self.assertRaises(SandboxError):
            DockerSandbox()

    def test_grader_compares_values_outside_candidate(self):
        from codine.evals import grade
        class FakeSandbox:
            def evaluate(self, source, inputs):
                self.inputs=inputs
                return {'status':'completed','values':[99],'exit_code':0}
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); (root/'task.py').write_text('def solve(x): return x')
            executor=FakeSandbox()
            result=grade(root,[{'args':[1],'expected':2}],executor)
            self.assertFalse(result['passed'])
            self.assertEqual(executor.inputs,[[1]])


@unittest.skipUnless(os.getenv('CODINE_DOCKER_TESTS')=='1','Opt in with CODINE_DOCKER_TESTS=1; requires a running Docker daemon and local image')
class DockerBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sandbox=DockerSandbox()

    def test_real_behavior_and_cleanup(self):
        result=self.sandbox.evaluate('def solve(x): return x * 2',[[3],[-1]])
        self.assertEqual(result['values'],[6,-2])
        self.assertEqual(result['status'],'completed')

    def test_host_file_and_key_are_not_available(self):
        with tempfile.TemporaryDirectory() as directory:
            sentinel=Path(directory)/'host-secret'
            sentinel.write_text('host-only-sentinel')
            source='import os\nfrom pathlib import Path\ndef solve():\n    return [Path('+repr(str(sentinel))+').exists(), os.getenv("NVIDIA_API_KEY"), os.getenv("OPENROUTER_API_KEY")]\n'
            with patch.dict(os.environ,{'NVIDIA_API_KEY':'test-host-key','OPENROUTER_API_KEY':'other-host-key'}):
                result=self.sandbox.evaluate(source,[[]])
            self.assertEqual(result['values'],[[False,None,None]])
            self.assertEqual(sentinel.read_text(),'host-only-sentinel')

    def test_network_is_blocked(self):
        source='''import socket
def solve():
    connection=socket.socket()
    connection.settimeout(0.5)
    try:
        connection.connect(('1.1.1.1',443))
        return 'connected'
    except OSError:
        return 'blocked'
    finally:
        connection.close()
'''
        self.assertEqual(self.sandbox.evaluate(source,[[]])['values'],['blocked'])

    def test_root_filesystem_read_only_and_nonroot(self):
        source='''import os
from pathlib import Path
def solve():
    try:
        Path('/usr/local/codine-test').write_text('bad')
        return 'writable'
    except OSError:
        return ['blocked',os.getuid()]
'''
        self.assertEqual(self.sandbox.evaluate(source,[[]])['values'],[['blocked',65534]])

    def test_timeout_and_output_limit(self):
        self.sandbox.timeout=2
        try:
            result=self.sandbox.evaluate('import time\ndef solve(): time.sleep(30)',[[]])
            self.assertEqual(result['status'],'timeout')
        finally:
            self.sandbox.timeout=5
        result=self.sandbox.evaluate('def solve():\n    while True: print("x"*10000)',[[]])
        self.assertEqual(result['status'],'output_limit')
        self.assertLessEqual(len(result['diagnostic']),1500)

    def test_interrupt_removes_the_created_container(self):
        import subprocess
        from unittest.mock import patch
        calls=[]
        control=self.sandbox._control
        def record(args):
            calls.append(args)
            return control(args)
        with patch.object(self.sandbox,'_control',side_effect=record), patch.object(__import__('selectors').DefaultSelector, 'select', side_effect=KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt):
                self.sandbox.evaluate('def solve(): return 1',[[]])
        name=calls[0][calls[0].index('--name')+1]
        self.assertEqual(calls[-1],['rm','--force',name])
        result=subprocess.run([self.sandbox.docker,'inspect',name],capture_output=True)
        self.assertNotEqual(result.returncode,0)

    def test_candidate_cannot_pass_by_exiting_zero(self):
        result=self.sandbox.evaluate('import os\nos._exit(0)',[[]])
        self.assertEqual(result['status'],'invalid_output')


if __name__=='__main__': unittest.main()
