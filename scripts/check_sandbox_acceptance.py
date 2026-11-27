"""Run M2's no-inference boundary gate; skipped checks cannot count as a pass."""
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SUITES = (
    'test_staging', 'test_repository_evals', 'test_repository_executor',
    'test_execution_mode', 'test_prepare_environment', 'test_execution_profiles',
    'test_command_policy', 'test_file_policy', 'test_command_streaming',
    'test_jobs', 'test_sandbox', 'test_milestone2_acceptance',
)


def main():
    os.chdir(ROOT)
    sys.path.insert(0,str(ROOT))
    sys.path.insert(0,str(ROOT/'tests'))
    # Opt-in must precede importing test modules, whose decorators inspect it.
    os.environ['CODINE_DOCKER_TESTS']='1'
    from codine.sandbox import DockerSandbox
    executor=DockerSandbox()  # Preflight local daemon/image, never pull or fall back.
    print('M2 sandbox gate; image='+executor.image+'; zero inference calls.',flush=True)
    suite=unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromName(name) for name in SUITES)
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    passed=result.wasSuccessful() and not result.skipped
    print('M2 boundary gate: '+('PASS' if passed else 'FAIL')+
          f'; tests={result.testsRun}; skipped={len(result.skipped)}; inference_calls=0.',flush=True)
    return 0 if passed else 1


if __name__=='__main__':
    raise SystemExit(main())
