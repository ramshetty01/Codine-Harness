import base64
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from codine.extension_manifest import snapshot_extension, LIMIT
from codine.extension_trust import ExtensionTrustStore, command
from codine.workspace import Workspace


class ExtensionTrustTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.home = Path(temp.name).resolve()
        self.root = self.home / 'project'; self.root.mkdir()
        self.ws = Workspace(self.root, lambda _: True, lambda _: None)
        self.store = ExtensionTrustStore(self.home / 'user-trust')
        self.package = self.root / 'extension'; self.package.mkdir()
        (self.package/'main.py').write_text('raise RuntimeError("MUST NEVER EXECUTE")\n')
        (self.package/'data.bin').write_bytes(b'\x00\xff')
        self.manifest = {'version': 1, 'name': 'example', 'entrypoint': 'main.py',
                         'files': ['main.py', 'data.bin'], 'capabilities': ['tools']}
        self.save_manifest()

    def save_manifest(self):
        (self.package/'extension.json').write_text(json.dumps(self.manifest))

    def review(self): return self.store.review(self.ws, 'extension/extension.json')

    def test_exact_review_allow_revoke_no_execution(self):
        snapshot = self.review()
        self.assertFalse(self.store.trusted(snapshot))
        self.assertEqual(base64.b64decode(snapshot['files'][0]['content_base64']), b'\x00\xff')
        approval = Mock(return_value=True)
        self.assertTrue(self.store.allow(self.ws, snapshot['digest'], approval))
        self.assertIn('host user privileges', approval.call_args.args[0])
        self.assertTrue(self.store.trusted(snapshot))
        self.assertTrue(ExtensionTrustStore(self.store.root).trusted(snapshot))
        self.assertEqual((self.store.root/'receipts.json').stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.ws.read_versions, {})
        self.assertTrue(self.store.revoke(snapshot['digest']))
        self.assertFalse(self.store.trusted(snapshot))

    def test_no_grant_without_review_or_on_decline(self):
        snapshot = self.review()
        with self.assertRaises(ValueError):
            ExtensionTrustStore(self.store.root).allow(self.ws, snapshot['digest'], lambda _:True)
        self.assertFalse(self.store.allow(self.ws, snapshot['digest'], lambda _:False))
        self.assertFalse(self.store.trusted(snapshot))

    def test_changed_source_or_manifest_invalidates_trust(self):
        snapshot = self.review()
        self.store.allow(self.ws, snapshot['digest'], lambda _:True)
        (self.package/'main.py').write_text('print("changed")')
        changed = self.review()
        self.assertFalse(self.store.trusted(changed))
        with self.assertRaises(ValueError): self.store.allow(self.ws, snapshot['digest'], lambda _:True)
        self.store.allow(self.ws, changed['digest'], lambda _:True)
        self.manifest['capabilities'].append('commands'); self.save_manifest()
        self.assertFalse(self.store.trusted(self.review()))

    def test_change_during_approval_cannot_grant(self):
        snapshot = self.review()
        def approve(_):
            (self.package/'main.py').write_text('changed during approval')
            return True
        with self.assertRaisesRegex(ValueError, 'during approval'):
            self.store.allow(self.ws, snapshot['digest'], approve)
        self.assertEqual(self.store.list(), [])

    def test_project_identity_and_file_replacement_are_bound(self):
        snapshot = self.review(); self.store.allow(self.ws, snapshot['digest'], lambda _:True)
        path = self.package/'main.py'
        other = self.package/'replacement.py'; other.write_bytes(path.read_bytes()); other.replace(path)
        self.assertFalse(self.store.trusted(self.review()))
        snapshot['project'] = str(self.home/'another')
        with self.assertRaises(ValueError): self.store.trusted(snapshot)

    def test_traversal_credentials_links_specialfiles_rejected(self):
        for name in ('../outside.py', '/outside.py', '.env', 'link.py', 'hard.py', 'pipe.py'):
            with self.subTest(name=name):
                self.manifest['files'] = ['main.py', name]; self.save_manifest()
                if name == '.env': (self.package/name).write_text('secret')
                if name == 'link.py': (self.package/name).symlink_to(self.package/'main.py')
                if name == 'hard.py': os.link(self.package/'data.bin', self.package/name)
                if name == 'pipe.py': os.mkfifo(self.package/name)
                with self.assertRaises((ValueError, OSError)): self.review()
                if (self.package/name).exists() and name in ('hard.py', 'pipe.py'): (self.package/name).unlink()

    def test_bounded_files_and_capabilities(self):
        for field,value in [('files', ['main.py']*33), ('files',['main.py','main.py']),
                            ('entrypoint','missing.py'), ('capabilities',['filesystem']), ('version', True)]:
            original = self.manifest[field]; self.manifest[field] = value; self.save_manifest()
            with self.assertRaises(ValueError): self.review()
            self.manifest[field] = original
        self.save_manifest()
        (self.package/'data.bin').write_bytes(b'x'*LIMIT)
        with self.assertRaises(ValueError): self.review()

    def test_file_policy_read_deny_and_ask_are_enforced(self):
        with patch.object(self.ws, '_authorize_read', side_effect=ValueError('Read declined')):
            with self.assertRaisesRegex(ValueError, 'declined'): self.review()
        with patch.object(self.ws, '_authorize_read', wraps=self.ws._authorize_read) as authorize:
            self.review()
        self.assertEqual(authorize.call_count, 3)

    def test_receipt_symlink_sharedmode_and_store_alias_rejected(self):
        snapshot = self.review(); self.store.allow(self.ws, snapshot['digest'], lambda _:True)
        receipt = self.store.root/'receipts.json'; receipt.chmod(0o644)
        with self.assertRaises(ValueError): self.store.trusted(snapshot)
        receipt.chmod(0o600)
        alias = self.home/'trust-link'; alias.symlink_to(self.store.root, target_is_directory=True)
        with self.assertRaises(OSError): ExtensionTrustStore(alias).trusted(snapshot)
        target = self.home/'fake.json'; target.write_text('{}'); receipt.unlink(); receipt.symlink_to(target)
        with self.assertRaises(OSError): self.store.trusted(snapshot)

    def test_model_filetools_cannot_read_or_write_trust_even_at_home(self):
        with patch('codine.extension_trust.TRUST_ROOT', self.store.root):
            broad = Workspace(self.home, lambda _:True, lambda _:None)
        for operation in ('read','write'):
            with self.assertRaisesRegex(ValueError, 'protected'):
                broad.resolve('user-trust/receipts.json', operation=operation)

    def test_cli_requires_exact_review_then_ui_approval(self):
        agent = SimpleNamespace(extension_trust=self.store)
        ui = SimpleNamespace(write=Mock(), approve=Mock(return_value=True))
        command('review extension/extension.json', agent, self.ws, ui)
        digest = next(iter(self.store.reviews))
        self.assertFalse(ui.approve.called)
        command('allow '+digest, agent, self.ws, ui)
        self.assertEqual(ui.approve.call_count, 1)
        command('revoke '+digest, agent, self.ws, ui)
        self.assertEqual(self.store.list(), [])

    def test_held_store_lock_fails_closed_without_waiting(self):
        import subprocess
        import sys
        import time
        snapshot = self.review()
        self.store.allow(self.ws, snapshot['digest'], lambda _:True)
        script = ('import fcntl,sys; '
                  'f=open(sys.argv[1], "r+"); fcntl.flock(f,fcntl.LOCK_EX); '
                  'print("locked",flush=True); sys.stdin.read()')
        child = subprocess.Popen([sys.executable, '-I', '-c', script, str(self.store.root/'lock')],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(child.stdout.readline().strip(), 'locked')
            start = time.monotonic()
            with self.assertRaisesRegex(ValueError, 'busy'):
                self.store.trusted(snapshot)
            with self.assertRaisesRegex(ValueError, 'busy'):
                self.store.revoke(snapshot['digest'])
            self.assertLess(time.monotonic()-start, 2)
        finally:
            child.communicate('', timeout=5)
        self.assertTrue(self.store.trusted(snapshot))

    def test_custom_store_inside_project_is_protected_on_review_and_allow(self):
        store = ExtensionTrustStore(self.root/'custom-trust')
        snapshot = store.review(self.ws, 'extension/extension.json')
        for operation in ('read', 'write'):
            with self.assertRaisesRegex(ValueError, 'protected'):
                self.ws.resolve('custom-trust/receipts.json', operation=operation)
        # A fresh workspace must acquire the same protection at allow time.
        fresh = Workspace(self.root, lambda _:True, lambda _:None)
        self.assertTrue(store.allow(fresh, snapshot['digest'], lambda _:True))
        for operation in ('read', 'write'):
            with self.assertRaisesRegex(ValueError, 'protected'):
                fresh.resolve('custom-trust/receipts.json', operation=operation)

    def test_atomic_write_failure_preserves_prior_grants(self):
        snapshot = self.review(); self.store.allow(self.ws, snapshot['digest'], lambda _:True)
        with patch('codine.extension_trust.os.replace', side_effect=OSError('disk failure')):
            with self.assertRaises(OSError): self.store.revoke(snapshot['digest'])
        self.assertTrue(self.store.trusted(snapshot))
        self.assertFalse(list(self.store.root.glob('pending-*')))

    def test_manifest_duplicate_fields_and_excessive_nesting_fail_as_value_errors(self):
        path = self.package/'extension.json'
        path.write_text(json.dumps(self.manifest)[:-1] + ', "capabilities": []}')
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            self.review()
        path.write_text('[' * 2000 + '0' + ']' * 2000)
        with self.assertRaises(ValueError):
            self.review()
        # Decoder recursion limits differ between supported Python versions.
        with patch('codine.extension_manifest.json.loads', side_effect=RecursionError):
            with self.assertRaisesRegex(ValueError, 'nesting'):
                self.review()
        path.write_text('{"version": NaN}')
        with self.assertRaisesRegex(ValueError, 'finite'):
            self.review()

    def test_trusted_snapshot_remains_exact_bytes_without_source_reread(self):
        snapshot = self.review()
        self.store.allow(self.ws, snapshot['digest'], lambda _:True)
        original = {row['path']: base64.b64decode(row['content_base64']) for row in snapshot['files']}
        (self.package/'main.py').write_text('print("UNAPPROVED SOURCE")')
        # Trust is about the returned bytes, not a permission to reopen current paths.
        with patch('codine.extension_trust.snapshot_extension', side_effect=AssertionError('unexpected source reread')):
            self.assertTrue(self.store.trusted(snapshot))
        self.assertIn(b'MUST NEVER EXECUTE', original['main.py'])
        self.assertNotIn(b'UNAPPROVED SOURCE', original['main.py'])
        self.assertFalse(self.store.trusted(self.review()))
        snapshot['files'][0]['content_base64'] = base64.b64encode(b'tampered').decode()
        with self.assertRaisesRegex(ValueError, 'digest mismatch'):
            self.store.trusted(snapshot)
