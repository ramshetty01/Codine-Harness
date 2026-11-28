"""User-only, exact-snapshot grants. No extension code executes in this module."""
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat

from .extension_manifest import canonical, snapshot_extension
from .instructions import _validate_user_guidance

TRUST_ROOT = Path.home() / '.config' / 'codine' / 'extension-trust'
WARNING = ('Python extensions run with your host user privileges, not in an OS sandbox. '
           'Declared capabilities describe Codine tools, commands, events and UI surfaces; '
           'they do not restrict filesystem, network or subprocess access.')


def binding(snapshot):
    unsigned = {k: v for k, v in snapshot.items() if k != 'digest'}
    digest = hashlib.sha256(canonical(unsigned)).hexdigest()
    if snapshot.get('digest') != digest: raise ValueError('Extension snapshot digest mismatch.')
    return {k: snapshot[k] for k in ('digest', 'project', 'project_identity', 'manifest_path',
                                    'manifest_identity', 'manifest_sha256')} | {
        'name': snapshot['manifest']['name'], 'capabilities': snapshot['manifest']['capabilities']}


class ExtensionTrustStore:
    def __init__(self, root=None):
        self.root = Path(root) if root is not None else TRUST_ROOT
        if not self.root.is_absolute(): raise ValueError('Trust directory must be absolute.')
        self.reviews = {}

    @contextlib.contextmanager
    def _locked(self):
        directory = os.open(self.root.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        lock = None
        try:
            for part in self.root.parts[1:]:
                _validate_user_guidance(os.fstat(directory), 'directory')
                try: os.mkdir(part, 0o700, dir_fd=directory)
                except FileExistsError: pass
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                os.close(directory)
                directory = child
            info = os.fstat(directory)
            _validate_user_guidance(info, 'parent')
            if info.st_uid != os.getuid(): raise ValueError('Trust directory must belong to this user.')
            lock = os.open('lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=directory)
            self._private(os.fstat(lock))
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield directory
        finally:
            if lock is not None: os.close(lock)
            os.close(directory)

    @staticmethod
    def _private(info):
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
                info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 1024*1024):
            raise ValueError('Trust records must be user-owned 0600 regular files with one link.')

    def _read(self, directory):
        try: fd = os.open('receipts.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        except FileNotFoundError: return {}
        try:
            self._private(os.fstat(fd))
            with os.fdopen(fd, 'rb', closefd=False) as stream: raw = stream.read(1024*1024+1)
            if len(raw) > 1024*1024: raise ValueError('Trust receipts exceed limit.')
            value = json.loads(raw)
            if not isinstance(value, dict) or value.get('version') != 1 or not isinstance(value.get('receipts'), dict):
                raise ValueError('Invalid trust receipt state.')
            return value['receipts']
        finally: os.close(fd)

    def _write(self, directory, receipts):
        raw = canonical({'version': 1, 'receipts': receipts})
        if len(raw) > 1024*1024: raise ValueError('Trust receipt limit reached; revoke old grants.')
        name = 'pending-' + secrets.token_hex(12)
        fd = os.open(name, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        try:
            with os.fdopen(fd, 'wb', closefd=False) as stream:
                stream.write(raw); stream.flush(); os.fsync(fd)
            os.replace(name, 'receipts.json', src_dir_fd=directory, dst_dir_fd=directory)
            os.fsync(directory)
        finally:
            os.close(fd)
            try: os.unlink(name, dir_fd=directory)
            except FileNotFoundError: pass

    def review(self, workspace, manifest_path):
        snapshot = snapshot_extension(workspace, manifest_path)
        if len(self.reviews) >= 16: self.reviews.pop(next(iter(self.reviews)))
        self.reviews[snapshot['digest']] = binding(snapshot)
        return snapshot

    def allow(self, workspace, digest, approve):
        reviewed = self.reviews.get(digest)
        if reviewed is None or reviewed['project'] != str(workspace.root):
            raise ValueError('Review this extension in the current process and project before allowing its digest.')
        snapshot = snapshot_extension(workspace, reviewed['manifest_path'])
        if binding(snapshot) != reviewed: raise ValueError('Extension changed since review; review again.')
        if not approve(WARNING + '\nAllow exact extension snapshot ' + digest + '?'):
            return False
        snapshot = snapshot_extension(workspace, reviewed['manifest_path'])
        if binding(snapshot) != reviewed: raise ValueError('Extension changed during approval; review again.')
        with self._locked() as directory:
            rows = self._read(directory)
            rows[digest] = reviewed
            self._write(directory, rows)
        return True

    def trusted(self, snapshot):
        expected = binding(snapshot)
        with self._locked() as directory:
            return self._read(directory).get(snapshot['digest']) == expected

    def list(self):
        with self._locked() as directory: return list(self._read(directory).values())

    def revoke(self, digest):
        with self._locked() as directory:
            rows = self._read(directory)
            found = rows.pop(digest, None) is not None
            self._write(directory, rows)
            return found


def command(argument, agent, workspace, ui):
    store = getattr(agent, 'extension_trust', None)
    if store is None:
        store = agent.extension_trust = ExtensionTrustStore()
    action, _, value = argument.strip().partition(' ')
    value = value.strip()
    if action == 'review' and value:
        snapshot = store.review(workspace, value)
        review = binding(snapshot) | {'entrypoint': snapshot['manifest']['entrypoint'],
                    'files': [{'path': row['path'], 'sha256': row['sha256']} for row in snapshot['files']]}
        ui.write(WARNING + '\n' + json.dumps(review, indent=2))
        ui.write('Read the declared source files before /trust allow ' + snapshot['digest'])
    elif action == 'allow' and value:
        ui.write('Trusted exact snapshot.' if store.allow(workspace, value, ui.approve) else 'Trust declined.')
    elif action == 'revoke' and value:
        ui.write('Trust revoked for future loads/actions.' if store.revoke(value) else 'No matching trust grant.')
    elif action in ('', 'list') and not value:
        ui.write(json.dumps(store.list(), indent=2))
    else: raise ValueError('Usage: /trust review MANIFEST | allow DIGEST | list | revoke DIGEST')
