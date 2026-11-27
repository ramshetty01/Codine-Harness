import json
import io
from contextlib import redirect_stderr
from pathlib import Path
import tempfile
import unittest
from codine.stress import run_stress, main


class StressRunnerTests(unittest.TestCase):
    def test_bounded_smoke_exercises_compaction_and_resources(self):
        report = run_stress(rounds=10, files=20, commands=1)
        self.assertTrue(report['passed'])
        self.assertEqual(report['archived_messages'], 21)
        self.assertGreater(report['semantic_fixture_summaries'], 0)
        self.assertEqual(report['inference_requests'], 0)
        self.assertIn('sha256', report['provenance'])

    def test_failed_attempt_is_preserved_and_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'failed.json'
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                main(['--rounds', '1', '--report', str(path)])
            before = path.read_bytes()
            self.assertEqual(json.loads(before)['status'], 'failed')
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                main(['--rounds', '10', '--report', str(path)])
            self.assertEqual(path.read_bytes(), before)
