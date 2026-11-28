"""User-owned input preferences, private prompt history and explicit editor input."""
from contextlib import contextmanager
import fcntl
import errno
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import tempfile
import uuid

from prompt_toolkit.history import History
from prompt_toolkit.keys import ALL_KEYS, KEY_ALIASES
from .transcripts import scrub

INPUT_PATH = Path.home() / '.config/codine/input.json'
HISTORY_PATH = Path.home() / '.local/state/codine/input-history.json'
EDITOR_ENV = {key: os.environ[key] for key in ('PATH', 'HOME', 'LANG', 'LC_ALL', 'TERM') if key in os.environ}
DEFAULT_KEYS = {'submit': ['enter'], 'newline': ['escape', 'enter'], 'history_search': ['c-r'], 'editor': ['c-x', 'c-e']}
MAX_HISTORY_BYTES = 256000
MAX_ENTRIES = 500
MAX_DRAFT = 64000
EDITOR_TEMP_ROOT = Path(tempfile.gettempdir()).resolve()


def outside_project(path, project):
    if path.resolve().is_relative_to(Path(project).resolve()):
        raise ValueError('Input settings and history must be outside the project.')


@contextmanager
def directory(path, create=False):
    path = Path(path).absolute()
    fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            if create:
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd); fd = child
        info = os.fstat(fd)
        if info.st_uid != os.getuid() or info.st_mode & 0o022:
            raise ValueError('Input state directory must be user-owned and not shared-writable.')
        yield fd
    finally:
        os.close(fd)


def private_read(fd, name, maximum):
    handle = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    with os.fdopen(handle, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1 or info.st_mode & 0o077 or info.st_size > maximum:
            raise ValueError('Input state must be a private regular file within its size limit.')
        raw = stream.read(maximum + 1)
    if len(raw) > maximum:
        raise ValueError('Input state exceeds its size limit.')
    return raw


def preferences(project, path=None):
    path = Path(path) if path is not None else INPUT_PATH
    outside_project(path, project)
    try:
        with directory(path.parent) as fd:
            value = json.loads(private_read(fd, path.name, 16000))
    except FileNotFoundError:
        value = {'version': 1}
    if not isinstance(value, dict) or set(value) - {'version', 'keys', 'editor_argv', 'history'} or type(value.get('version')) is not int or value['version'] != 1:
        raise ValueError('Input settings require version 1 with keys, editor_argv or history.')
    keys = {key: list(sequence) for key, sequence in DEFAULT_KEYS.items()}
    overrides = value.get('keys', {})
    if not isinstance(overrides, dict) or set(overrides) - keys.keys():
        raise ValueError('Configurable actions: submit, newline, history_search, editor.')
    keys.update(overrides)
    reserved = {'c-c', 'c-d', 'c-z', 'c-m', 'c-j', 'c-i', 'tab', 's-tab', 'bracketed-paste', '<any>', '<sigint>'}
    for sequence in keys.values():
        if not isinstance(sequence, list) or not 1 <= len(sequence) <= 2 or any(not isinstance(k, str) or (k not in ALL_KEYS and k not in KEY_ALIASES) or k in reserved for k in sequence):
            raise ValueError('Bindings require 1–2 supported keys; interrupts, EOF, tab, paste and control aliases are reserved.')
        if sequence == ['escape']:
            raise ValueError('Escape alone is reserved for cancellation.')
    sequences = [[KEY_ALIASES.get(k, k) for k in sequence] for sequence in keys.values()]
    if any(a == b[:len(a)] or b == a[:len(b)] for i, a in enumerate(sequences) for b in sequences[i + 1:]):
        raise ValueError('Bindings must not overlap or be prefixes of one another.')
    editor = value.get('editor_argv')
    if editor is not None:
        if not isinstance(editor, list) or not 1 <= len(editor) <= 20 or any(not isinstance(arg, str) or not arg or '\x00' in arg or len(arg) > 1000 for arg in editor):
            raise ValueError('editor_argv must contain 1–20 literal command arguments.')
        executable = Path(editor[0])
        if not executable.is_absolute() or not executable.is_file() or not os.access(executable, os.X_OK):
            raise ValueError('Editor executable must be an absolute executable file path.')
        outside_project(executable, project)
        for argument in editor[1:]:
            candidate = Path(argument)
            if candidate.exists():
                outside_project(candidate, project)
    history = value.get('history', True)
    if type(history) is not bool:
        raise ValueError('history must be true or false.')
    return {'keys': keys, 'editor_argv': editor, 'history': history}


class PrivateHistory(History):
    def __init__(self, path, redactor):
        super().__init__()
        self.path, self.redactor = Path(path), redactor
        self.failure = None

    @contextmanager
    def locked(self):
        with directory(self.path.parent, create=True) as fd:
            lock = os.open(self.path.name + '.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=fd)
            try:
                info = os.fstat(lock)
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1 or info.st_mode & 0o077:
                    raise ValueError('Unsafe prompt history lock.')
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                yield fd
            finally:
                os.close(lock)

    def read(self, fd):
        try:
            raw = private_read(fd, self.path.name, MAX_HISTORY_BYTES)
        except FileNotFoundError:
            return []
        data = json.loads(raw)
        if not isinstance(data, dict) or set(data) != {'version', 'entries'} or type(data['version']) is not int or data['version'] != 1:
            raise ValueError('Invalid prompt history version.')
        entries = data['entries']
        if not isinstance(entries, list) or len(entries) > MAX_ENTRIES or any(not isinstance(v, str) or len(v) > 16000 for v in entries):
            raise ValueError('Invalid prompt history entries.')
        return [scrub(entry, self.redactor()) for entry in entries]

    def load_history_strings(self):
        try:
            with self.locked() as fd:
                return reversed(self.read(fd))
        except (OSError, ValueError, RecursionError):
            self.failure = 'Prompt history unavailable; existing file preserved. Inspect user state permissions/content.'
            return []

    def append_string(self, string):
        if not string.strip() or len(string) > 16000:
            return
        clean = scrub(string, self.redactor())
        super().append_string(clean)
        del self._loaded_strings[MAX_ENTRIES:]

    def store_string(self, string):
        try:
            with self.locked() as fd:
                entries = self.read(fd)
                if not entries or entries[-1] != string:
                    entries.append(string)
                entries = entries[-MAX_ENTRIES:]
                while True:
                    raw = json.dumps({'version': 1, 'entries': entries}, ensure_ascii=False).encode()
                    if len(raw) <= MAX_HISTORY_BYTES:
                        break
                    entries.pop(0)
                temporary = '.history-' + uuid.uuid4().hex
                handle = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
                try:
                    with os.fdopen(handle, 'wb') as stream:
                        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
                    os.rename(temporary, self.path.name, src_dir_fd=fd, dst_dir_fd=fd)
                    os.fsync(fd)
                finally:
                    try: os.unlink(temporary, dir_fd=fd)
                    except FileNotFoundError: pass
            self.failure = None
        except (OSError, ValueError, RecursionError):
            self.failure = 'Prompt history persistence could not be confirmed. Inspect user state or concurrent writer.'


def edit_text(text, argv, *, redactor, terminal_fd=None, timeout=600):
    if argv is None:
        raise ValueError('Set editor_argv in the user input settings first; $EDITOR is not executed.')
    text = scrub(text, redactor)
    if len(text.encode()) > MAX_DRAFT:
        raise ValueError('Editor draft exceeds 64 KB.')
    with tempfile.TemporaryDirectory(prefix='codine-draft-', dir=EDITOR_TEMP_ROOT) as folder:
        path = Path(folder).resolve() / 'prompt.txt'
        handle = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(handle, 'w') as stream:
            stream.write(text)
        process = None
        foreground = None
        old_sigttou = signal.getsignal(signal.SIGTTOU)
        try:
            signal.signal(signal.SIGTTOU, signal.SIG_IGN)
            if terminal_fd is not None:
                try:
                    foreground = os.tcgetpgrp(terminal_fd)
                except OSError as error:
                    if error.errno != errno.ENOTTY:
                        raise
                    # IDE/test PTYs may be readable without a controlling TTY.
                    terminal_fd = None
            process = subprocess.Popen([*argv, str(path)], env=EDITOR_ENV, cwd=path.parent, process_group=0)
            if terminal_fd is not None:
                os.tcsetpgrp(terminal_fd, process.pid)
                os.killpg(process.pid, signal.SIGCONT)
            code = process.wait(timeout=timeout)
            if code != 0:
                raise ValueError('External editor did not exit successfully; original draft preserved.')
            with directory(path.parent) as fd:
                raw = private_read(fd, path.name, MAX_DRAFT)
            return scrub(raw.decode('utf-8'), redactor)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            raise ValueError('External editor cancelled or timed out; original draft preserved.') from None
        finally:
            try:
                if process is not None:
                    try: os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError: pass
                    process.wait(timeout=5)
            finally:
                try:
                    if foreground is not None:
                        os.tcsetpgrp(terminal_fd, foreground)
                finally:
                    signal.signal(signal.SIGTTOU, old_sigttou)
