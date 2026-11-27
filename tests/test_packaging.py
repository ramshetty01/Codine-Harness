"""Path/restart compatibility checks; installed-wheel smoke is scripts/check_install.py."""
import os
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import patch

from codine.paths import APP_ROOT, eval_resource


class PackagePathsTests(unittest.TestCase):
    def paths(self, root, env):
        target = root / 'site-packages' / 'codine' / 'paths.py'
        target.parent.mkdir(parents=True)
        target.write_text((APP_ROOT / 'codine' / 'paths.py').read_text())
        with patch.dict(os.environ, env, clear=True):
            return runpy.run_path(str(target))

    def test_installed_paths_use_user_directories(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            values = self.paths(root, {'HOME': str(root / 'home')})
            self.assertFalse(values['IS_CHECKOUT'])
            self.assertEqual(values['CONFIG_ROOT'], root / 'home' / '.config' / 'codine')
            self.assertEqual(values['STATE_ROOT'], root / 'home' / '.local' / 'state' / 'codine')
            self.assertFalse(values['CONFIG_ROOT'].exists())

    def test_explicit_home_overrides_installed_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            values = self.paths(root, {'CODINE_HOME': str(root / 'custom')})
            self.assertEqual(values['CONFIG_ROOT'], (root / 'custom').resolve())
            self.assertEqual(values['STATE_ROOT'], (root / 'custom' / 'state').resolve())

    def test_fixtures_and_path_traversal(self):
        self.assertTrue(eval_resource('tasks.json').is_file())
        with self.assertRaises(ValueError):
            eval_resource('../../.env')
