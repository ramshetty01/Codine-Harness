"""Explicit, bounded repository snapshots. Never execute or recursively copy files."""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import unicodedata


class StagingError(ValueError):
    """The selected files cannot be safely included in a snapshot."""


EXCLUDED = frozenset({'.git', '.hg', '.svn', '.codine', '.sessions', '.venv', 'venv',
    'node_modules', '__pycache__', '.pytest_cache', '.mypy_cache', '.ruff_cache',
    'dist', 'build', 'target', 'vendor', 'coverage', 'htmlcov', '.next', '.tox', '.nox', '.cache', '.ssh', '.aws', '.gnupg', '.config',
    '.npmrc', '.pypirc', '.netrc', 'credentials', 'secrets', 'credentials.json',
    'secrets.json', 'secrets.yaml', 'secrets.yml'})
EXTENSIONS = frozenset({'.py', '.pyi', '.js', '.jsx', '.ts', '.tsx', '.mjs', '.cjs',
    '.json', '.toml', '.yaml', '.yml', '.ini', '.cfg', '.txt', '.md', '.rst',
    '.rs', '.go', '.c', '.h', '.cpp', '.hpp', '.java', '.sh', '.css', '.html',
    '.sql', '.xml', '.lock'})
NAMES = frozenset({'dockerfile', 'makefile', 'justfile', '.gitignore', '.dockerignore'})


@dataclass(frozen=True)
class StagingLimits:
    max_files: int = 256
    max_file_bytes: int = 1_000_000
    max_total_bytes: int = 8_000_000
    max_path_bytes: int = 240

    def __post_init__(self):
        for value in (self.max_files, self.max_file_bytes, self.max_total_bytes, self.max_path_bytes):
            if type(value) is not int or value <= 0:
                raise ValueError('Staging limits must be positive integers.')


@dataclass(frozen=True)
class StagedFile:
    path: str
    content: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class Manifest:
    files: tuple[StagedFile, ...]

    @property
    def total_bytes(self):
        return sum(entry.size_bytes for entry in self.files)

    @property
    def digest(self):
        metadata = [(entry.path, entry.sha256, entry.size_bytes) for entry in self.files]
        return hashlib.sha256(json.dumps(metadata, separators=(',', ':')).encode()).hexdigest()

    def as_text_files(self):
        """Return a new mapping suitable for a container input payload (not host mounts)."""
        return {entry.path: entry.content for entry in self.files}


def _validate_path(name, limits):
    if not isinstance(name, str) or not name or len(name.encode('utf-8')) > limits.max_path_bytes:
        raise StagingError('Invalid or oversized manifest path.')
    path = PurePosixPath(name)
    if (path.is_absolute() or str(path) != name or '\\' in name or ':' in name or
            any(ord(char) < 32 or ord(char) == 127 for char in name) or
            any(part in {'.', '..'} for part in path.parts)):
        raise StagingError('Manifest paths must be normalized relative POSIX paths.')
    for part in path.parts:
        lower = part.lower()
        if (lower in EXCLUDED or lower.startswith(('.env', 'secrets.', 'credentials.')) or
                lower.endswith('.egg-info') or
                lower.endswith(('.pem', '.key', '.p12', '.pfx', '.crt')) or
                lower.startswith(('id_rsa', 'id_ed25519'))):
            raise StagingError('Credential, internal, or generated paths cannot be staged.')
    if path.suffix.lower() not in EXTENSIONS and path.name.lower() not in NAMES:
        raise StagingError('Only supported source, configuration, and documentation files may be staged.')
    return path.parts


def _read_file(root_fd, parts, limit, *, validate=None):
    """Walk beneath an already opened root; never follow symlinks at any component."""
    directory = os.dup(root_fd)
    descriptor = None
    try:
        if validate is not None: validate(os.fstat(directory), 'directory')
        for component in parts[:-1]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
            if validate is not None: validate(os.fstat(directory), 'directory')
        if validate is not None: validate(os.fstat(directory), 'parent')
        descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        before = os.fstat(descriptor)
        if validate is not None: validate(before, 'file')
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise StagingError('Only regular files with one hard link may be staged.')
        if before.st_size > limit:
            raise StagingError('Staged file exceeds byte budget.')
        data = bytearray()
        while len(data) <= limit:
            chunk = os.read(descriptor, min(65536, limit + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        after = os.fstat(descriptor)
        if validate is not None: validate(after, 'file')
        identity = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns,
                                  value.st_ctime_ns, value.st_nlink)
        if len(data) > limit or identity(before) != identity(after):
            raise StagingError('File changed while staging or exceeded byte budget.')
        if b'\0' in data:
            raise StagingError('Binary files are not supported in text manifests.')
        return bytes(data)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(directory)


def snapshot_repository(root, paths, limits=None):
    """Snapshot explicit UTF-8 files; fail closed on any rejected selection.

    POSIX descriptor traversal is required. The root is caller-selected and must
    itself be a real directory. No filesystem paths or environment are implicit.
    """
    limits = limits or StagingLimits()
    if isinstance(paths, (str, bytes)):
        raise StagingError('Select a sequence of paths, not a single string.')
    selected = []
    seen = set()
    for name in paths:
        if len(selected) >= limits.max_files:
            raise StagingError('Manifest exceeds file count limit.')
        parts = _validate_path(name, limits)
        collision_key = unicodedata.normalize('NFC', name).casefold()
        if collision_key in seen:
            raise StagingError('Duplicate or case-colliding manifest paths.')
        seen.add(collision_key)
        selected.append((name, parts))
    if not selected:
        raise StagingError('Select at least one file.')
    if not hasattr(os, 'O_NOFOLLOW') or os.open not in os.supports_dir_fd:
        raise StagingError('Safe staging requires POSIX no-follow directory descriptors.')
    root_fd = None
    try:
        root_fd = os.open(Path(root), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        files = []
        total = 0
        for name, parts in sorted(selected):
            data = _read_file(root_fd, parts, min(limits.max_file_bytes, limits.max_total_bytes - total))
            total += len(data)
            files.append(StagedFile(name, data.decode('utf-8'), hashlib.sha256(data).hexdigest(), len(data)))
        return Manifest(tuple(files))
    except (OSError, UnicodeError) as error:
        raise StagingError('Cannot safely read selected UTF-8 files: ' + str(error)) from error
    finally:
        if root_fd is not None:
            os.close(root_fd)
