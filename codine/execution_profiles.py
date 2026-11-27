"""Explicit user-owned Docker execution grants; model text cannot choose secrets."""
from dataclasses import dataclass, field
from contextlib import contextmanager
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat

NAME = re.compile(r'[A-Z][A-Z0-9_]{0,63}\Z')
IMAGE = re.compile(r'sha256:[0-9a-f]{64}\Z')
# Credentials cannot change interpreter/loader/bootstrap behavior.
CONTROL_NAMES = {'PATH', 'HOME', 'ENV', 'BASH_ENV', 'SHELLOPTS', 'IFS', 'LANG', 'TZ',
                 'TMPDIR', 'NODE_OPTIONS', 'RUBYOPT', 'PERL5OPT', 'GCONV_PATH', 'CDPATH'}
CONTROL_PREFIXES = ('LD_', 'DYLD_', 'PYTHON', 'BASH_', 'DOCKER_', 'CODINE_')


def credential_name(name):
    return (isinstance(name, str) and NAME.fullmatch(name) is not None and
            name not in CONTROL_NAMES and not name.startswith(CONTROL_PREFIXES) and
            any(word in name for word in ('TOKEN', 'KEY', 'SECRET', 'PASSWORD', 'CREDENTIAL')))


def _load_private_source(source):
    """No symlink components or hardlinks; check ownership and a stable read."""
    directory = os.open(source.anchor, os.O_RDONLY | os.O_DIRECTORY)
    descriptor = None
    try:
        for part in source.parts[1:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        descriptor = os.open(source.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        before = os.fstat(descriptor)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid() or
                before.st_nlink != 1 or before.st_mode & 0o022 or before.st_size > 65536):
            raise ValueError('Execution profile must be user-owned, non-shared, and under 64 KB.')
        data = bytearray()
        while len(data) <= 65536:
            chunk = os.read(descriptor, min(8192, 65537 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        after = os.fstat(descriptor)
        identity = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns,
                                  value.st_ctime_ns, value.st_nlink)
        if len(data) > 65536 or identity(before) != identity(after):
            raise ValueError('Execution profile changed while reading or exceeded 64 KB.')
        return json.loads(data)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError('Cannot safely load execution profile.') from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(directory)


@dataclass(frozen=True)
class ExecutionGrant:
    id: str
    cwd: str
    argv: tuple[str, ...]
    network: str = 'none'
    credentials: tuple[str, ...] = ()

    @property
    def expands_access(self):
        return self.network != 'none' or bool(self.credentials)

    def describe(self):
        network = ('bridge (general outbound network; no domain filtering)' if self.network == 'bridge'
                   else 'none (offline)')
        return (f'Execution profile rule: {self.id}\nNetwork: {network}\n'
                f'Forwarded shell credential names: {", ".join(self.credentials) or "none"}\n'
                'Filesystem: selected snapshot only; no host mounts; outputs remain ephemeral.\n' +
                ('All command stdout/stderr will be withheld from the UI, model and session; only status is returned.\n'
                 if self.credentials else '') +
                ('Fresh approval is required for this command; policy allow rules cannot waive it.'
                 if self.expands_access else 'No filesystem or network expansion.'))


@dataclass(frozen=True)
class ShellCredentials:
    """Captured once before dotenv. Never attach this object to session metadata."""
    values: dict = field(repr=False)

    @contextmanager
    def restart_environment(self):
        """Do not promote dotenv-added values into shell credentials after exec."""
        previous = {name: os.environ.get(name) for name in self.values}
        try:
            for name, value in self.values.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
            yield
        finally:
            # Reached only if exec failed or tests injected a replacement executor.
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

    def check_arguments(self, grant):
        for name in grant.credentials:
            value = self.values.get(name)
            if isinstance(value, str) and value and any(value in argument for argument in grant.argv):
                raise ValueError('Credential values must not appear in command arguments.')

    def for_grant(self, grant):
        self.check_arguments(grant)
        selected = {}
        for name in grant.credentials:
            value = self.values.get(name)
            if not isinstance(value, str) or not value or '\0' in value or len(value.encode()) > 16384:
                raise ValueError(f'Missing or invalid startup-shell credential: {name}.')
            selected[name] = value
        if sum(len(value.encode()) for value in selected.values()) > 32768:
            raise ValueError('Selected credentials exceed 32 KB.')
        return selected


@dataclass(frozen=True)
class ExecutionProfile:
    source: Path
    project: str
    image: str
    rules: tuple[ExecutionGrant, ...]

    @classmethod
    def load(cls, path):
        source = Path(path).expanduser().absolute()
        if '..' in source.parts:
            raise ValueError('Execution profile path must not contain traversal.')
        raw = _load_private_source(source)
        if (not isinstance(raw, dict) or set(raw) != {'version', 'executor', 'project', 'image', 'filesystem', 'rules'} or
                type(raw['version']) is not int or raw['version'] != 1 or raw['executor'] != 'docker' or
                raw['filesystem'] != 'staged-only' or not isinstance(raw['image'], str) or
                not IMAGE.fullmatch(raw['image'])):
            raise ValueError('Execution profile requires version 1, Docker, an immutable image ID and staged-only filesystem.')
        project = raw['project']
        if not isinstance(project, str) or not Path(project).is_absolute() or str(Path(project).resolve()) != project:
            raise ValueError('Execution profile project must be an exact canonical absolute path.')
        if source.is_relative_to(project):
            raise ValueError('Execution profiles must live outside the project the agent can edit.')
        if not isinstance(raw['rules'], list) or not 1 <= len(raw['rules']) <= 32:
            raise ValueError('Execution profile requires 1–32 exact command rules.')
        rules, ids, matches = [], set(), set()
        for item in raw['rules']:
            if not isinstance(item, dict) or set(item) != {'id', 'cwd', 'argv', 'network', 'credentials'}:
                raise ValueError('Execution rules require id/cwd/argv/network/credentials.')
            name, cwd, argv = item['id'], item['cwd'], item['argv']
            if not isinstance(name, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', name) or name in ids:
                raise ValueError('Execution rule IDs must be unique simple names.')
            if not isinstance(cwd, str):
                raise ValueError('Execution rule cwd must be a relative path.')
            relative = PurePosixPath(cwd)
            if relative.is_absolute() or str(relative) != cwd or '..' in relative.parts or '\\' in cwd or '\0' in cwd:
                raise ValueError('Execution rule cwd must be a normalized relative path.')
            if (not isinstance(argv, list) or not 1 <= len(argv) <= 128 or
                    any(not isinstance(arg, str) or '\0' in arg for arg in argv) or not argv[0] or
                    sum(len(arg.encode()) for arg in argv) > 32768):
                raise ValueError('Execution rule argv must be a bounded nonempty array.')
            credentials = item['credentials']
            if (not isinstance(credentials, list) or len(credentials) > 8 or
                    any(not credential_name(name) for name in credentials) or len(set(credentials)) != len(credentials)):
                raise ValueError('Credential names must be unique, bounded shell credential names; runtime-control variables are forbidden.')
            if item['network'] not in ('none', 'bridge') or (cwd, tuple(argv)) in matches:
                raise ValueError('Execution rules require unique exact commands and network none or bridge.')
            ids.add(name); matches.add((cwd, tuple(argv)))
            rules.append(ExecutionGrant(name, cwd, tuple(argv), item['network'], tuple(credentials)))
        return cls(source, project, raw['image'], tuple(rules))

    def check_scope(self, project, executor, image):
        if executor != 'docker' or str(Path(project).resolve()) != self.project or image != self.image:
            raise ValueError('Execution profile does not match this exact project, Docker executor and immutable image.')

    def grant(self, *, project, image, cwd, argv):
        self.check_scope(project, 'docker', image)
        return next((rule for rule in self.rules if rule.cwd == cwd and rule.argv == tuple(argv)), None)

    def capture_shell(self):
        names = {name for rule in self.rules for name in rule.credentials}
        return ShellCredentials({name: os.environ.get(name) for name in names})
