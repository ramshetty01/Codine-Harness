import json
import os
from pathlib import Path
import tempfile
import unittest
from codine.diagnostics import create_bundle


class DiagnosticTests(unittest.TestCase):
    def test_private_content_free_and_read_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sessions = root / '.codine/sessions'
            sessions.mkdir(parents=True)
            source = sessions / 'test.json'
            source.write_text(json.dumps({'version': 1, 'messages': [{'role': 'system', 'content': 'SECRET'}],
                                          'model': 'SECRET', 'project': 'SECRET', 'billing': {'pending': '.05'}}))
            before = source.read_bytes()
            output = create_bundle(root, 'test')
            self.assertEqual(source.read_bytes(), before)
            self.assertNotIn('SECRET', output.read_text())
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            self.assertTrue(json.loads(output.read_text())['session']['pending_charge'])
            source.write_text('{invalid SECRET')
            self.assertEqual(json.loads(create_bundle(root, 'test').read_text())['category'], 'invalid_session')

    def test_symlink_output_refused_and_future_schema_untouched(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / '.codine/sessions').mkdir(parents=True)
            source = root / '.codine/sessions/test.json'
            source.write_text('{"version": 999}')
            output = create_bundle(root, 'test')
            self.assertEqual(json.loads(output.read_text())['category'], 'unsupported_version')
            output.unlink()
            output.parent.rmdir()
            output.parent.symlink_to(root, target_is_directory=True)
            with self.assertRaises(OSError):
                create_bundle(root)
            self.assertEqual(source.read_text(), '{"version": 999}')
