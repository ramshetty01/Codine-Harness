from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from codine.workspace import Workspace


class ManagedStateTests(unittest.TestCase):
    def test_broad_workspace_cannot_write_managed_ledger_or_future_declaration(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary).resolve()
            state = home / '.local/state/codine'
            state.mkdir(parents=True)
            ledger = state / 'evals/budget.json'
            ledger.parent.mkdir()
            ledger.write_text('existing-spend')
            declaration = home / '.config/codine/model-capabilities.json'
            with patch('codine.paths.STATE_ROOT', state), patch('codine.capabilities.USER_CAPABILITIES', declaration):
                workspace = Workspace(home, lambda _: True, lambda _: None)
            for path in (ledger, declaration):
                relative = path.relative_to(home).as_posix()
                with self.assertRaisesRegex(ValueError, 'protected'):
                    workspace.write_file(relative, 'reset')
                with self.assertRaisesRegex(ValueError, 'protected'):
                    workspace.resolve(relative)
            self.assertEqual(ledger.read_text(), 'existing-spend')
            self.assertFalse(declaration.exists())
            workspace.write_file('ordinary.txt', 'allowed')
            self.assertEqual((home / 'ordinary.txt').read_text(), 'allowed')
