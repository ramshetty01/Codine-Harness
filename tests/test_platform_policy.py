import contextlib
import io
import unittest
from unittest.mock import patch
from codine.launcher import main, platform_problem


class PlatformPolicyTests(unittest.TestCase):
    def test_supported_matrix_and_explicit_unsupported_platforms(self):
        for platform in ('darwin', 'linux'):
            for minor in (11, 12, 13, 14):
                self.assertIsNone(platform_problem(platform, (3, minor)))
        self.assertIn('Python 3.11', platform_problem('linux', (3, 10)))
        for platform in ('win32', 'cygwin', 'freebsd14'):
            self.assertIn('macOS and Linux', platform_problem(platform, (3, 14)))

    def test_unsupported_launch_returns_before_runtime_import_or_state(self):
        output = io.StringIO()
        with patch('codine.launcher.sys.platform', 'win32'), contextlib.redirect_stderr(output), \
                patch.dict('sys.modules', {'codine.cli': None}):
            self.assertEqual(main(['--check']), 2)
        self.assertIn('Native Windows is unsupported', output.getvalue())
        self.assertNotIn('Traceback', output.getvalue())
