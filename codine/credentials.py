"""User-owned plaintext API credentials. Never serialize these into session state."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import secrets
import stat

AUTH_ROOT = Path.home() / '.config' / 'codine' / 'credentials'
MAX_BYTES = 65536


def identity(provider, endpoint=None):
    if provider not in ('openrouter', 'nvidia', 'custom'):
        raise ValueError('Unsupported credential provider.')
    if provider == 'custom':
        from .custom_endpoint import canonical_endpoint
        endpoint = canonical_endpoint(endpoint)
    elif endpoint is not None:
        raise ValueError('Built-in provider credentials do not accept a custom endpoint.')
    return provider + (':' + endpoint if endpoint else '')


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result: raise ValueError('Duplicate credential record fields.')
        result[key] = value
    return result


def _private(info):
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
        info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > MAX_BYTES):
        raise ValueError('Credential records must be user-owned 0600 regular files with one link.')


def _version(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_nlink, info.st_mode, info.st_uid)


class CredentialStore:
    def __init__(self, root=None):
        self.root = Path(root if root is not None else AUTH_ROOT).absolute()

    @contextmanager
    def _locked(self, create=False):
        directory = os.open(self.root.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        lock = None
        try:
            for part in self.root.parts[1:]:
                info = os.fstat(directory)
                if info.st_uid not in (0, os.getuid()) or (info.st_mode & 0o022 and not (info.st_uid == 0 and info.st_mode & stat.S_ISVTX)):
                    raise ValueError('Credential directory ancestors must not be shared-writable.')
                if create:
                    try: os.mkdir(part, 0o700, dir_fd=directory)
                    except FileExistsError: pass
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                os.close(directory); directory = child
            info = os.fstat(directory)
            if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
                raise ValueError('Credential directory must be user-owned with mode 0700.')
            lock = os.open('lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=directory)
            _private(os.fstat(lock))
            try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError: raise ValueError('Credential store is busy; retry later.') from None
            yield directory
        finally:
            if lock is not None: os.close(lock)
            os.close(directory)

    def _read(self, directory):
        try: handle = os.open('keys.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        except FileNotFoundError: return {}
        try:
            before = os.fstat(handle); _private(before)
            with os.fdopen(handle, 'rb', closefd=False) as stream: raw = stream.read(MAX_BYTES + 1)
            after = os.fstat(handle); _private(after)
            if len(raw) > MAX_BYTES or _version(before) != _version(after):
                raise ValueError('Credential store changed during read.')
            try: data = json.loads(raw, object_pairs_hook=_pairs)
            except (ValueError, UnicodeError): raise ValueError('Invalid credential store.') from None
            if not isinstance(data, dict) or set(data) != {'version', 'keys'} or type(data['version']) is not int or data['version'] != 1 or not isinstance(data['keys'], dict) or len(data['keys']) > 32:
                raise ValueError('Invalid credential store.')
            for name, key in data['keys'].items():
                if not isinstance(name, str) or not (name in ('openrouter', 'nvidia') or name.startswith('custom:')):
                    raise ValueError('Invalid credential store provider.')
                validate_key(key)
            return data['keys']
        finally: os.close(handle)

    def get(self, provider, endpoint=None):
        name = identity(provider, endpoint)
        try:
            with self._locked() as directory: return self._read(directory).get(name, '')
        except FileNotFoundError: return ''

    def _change(self, provider, value, endpoint=None):
        name = identity(provider, endpoint)
        with self._locked(create=True) as directory:
            rows = self._read(directory)
            if value is None: rows.pop(name, None)
            else: validate_key(value); rows[name] = value
            raw = json.dumps({'version': 1, 'keys': rows}).encode()
            if len(rows) > 32 or len(raw) > MAX_BYTES: raise ValueError('Credential storage limit reached.')
            temporary = 'pending-' + secrets.token_hex(16)
            handle = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600, dir_fd=directory)
            try:
                with os.fdopen(handle, 'wb', closefd=False) as stream:
                    stream.write(raw); stream.flush(); os.fsync(handle)
                os.replace(temporary, 'keys.json', src_dir_fd=directory, dst_dir_fd=directory)
                os.fsync(directory)
            finally:
                os.close(handle)
                try: os.unlink(temporary, dir_fd=directory)
                except FileNotFoundError: pass

    def set(self, provider, key, endpoint=None): self._change(provider, key, endpoint)
    def delete(self, provider, endpoint=None): self._change(provider, None, endpoint)


def validate_key(key):
    if not isinstance(key, str) or not 8 <= len(key) <= 4096 or any(ord(char) < 33 or ord(char) > 126 for char in key):
        raise ValueError('API key must contain 8–4096 printable ASCII characters without whitespace.')
