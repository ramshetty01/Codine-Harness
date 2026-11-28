"""Bounded private tool-output snapshots, addressed by immutable content hash."""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import uuid

from .diagnostics import _directory
from .staging import _read_file

MAX_FILE = 2_000_000
MAX_TOTAL = 16_000_000
MAX_FILES = 64
THRESHOLD = 12000


class ArtifactStore:
    def __init__(self, project, workspace=None):
        self.root = Path(project).resolve()
        self.workspace = workspace

    @contextmanager
    def directory(self, create=False):
        root = internal = directory = lock = None
        try:
            root = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            internal = _directory(root, '.codine', create)
            directory = _directory(internal, 'artifacts', create)
            lock = os.open('.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=directory)
            info = os.fstat(lock)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise ValueError('Unsafe artifact lock.')
            try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError: raise ValueError('Artifact store is busy; retry the read later.') from None
            yield directory
        finally:
            for descriptor in (lock, directory, internal, root):
                if descriptor is not None:
                    os.close(descriptor)

    def put(self, tool, content):
        from .storage_lock import mutation
        with mutation(self.root):
            return self._put_storage(tool, content)

    def _put_storage(self, tool, content):
        raw = json.dumps({'tool': tool, 'content': content}, ensure_ascii=False, sort_keys=True).encode()
        if len(raw) > MAX_FILE:
            raise ValueError('Output exceeds the 2 MB artifact limit.')
        ident = hashlib.sha256(raw).hexdigest()
        name = ident + '.json'
        with self.directory(create=True) as directory:
            try:
                existing = _read_file(directory, (name,), MAX_FILE)
                if existing != raw:
                    raise ValueError('Artifact identity mismatch.')
                return ident
            except FileNotFoundError:
                pass
            count, total = 0, 0
            for entry in os.listdir(directory):
                if not re.fullmatch(r'[0-9a-f]{64}\.json', entry):
                    continue
                info = os.stat(entry, dir_fd=directory, follow_symlinks=False)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise ValueError('Unsafe artifact entry.')
                count += 1
                total += info.st_size
            if count >= MAX_FILES or total + len(raw) > MAX_TOTAL:
                raise ValueError('Artifact quota is full (64 files / 16 MB). Existing references were preserved.')
            temporary = '.write-' + uuid.uuid4().hex
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
            try:
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.rename(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
                os.fsync(directory)
            finally:
                try:
                    os.unlink(temporary, dir_fd=directory)
                except FileNotFoundError:
                    pass
        return ident

    def record(self, artifact_id):
        if not isinstance(artifact_id, str) or not re.fullmatch(r'[0-9a-f]{64}', artifact_id):
            raise ValueError('Invalid artifact ID.')
        with self.directory() as directory:
            raw = _read_file(directory, (artifact_id + '.json',), MAX_FILE)
        if hashlib.sha256(raw).hexdigest() != artifact_id:
            raise ValueError('Artifact content changed; refusing corrupted snapshot.')
        record = json.loads(raw)
        if (not isinstance(record, dict) or set(record) != {'tool', 'content'} or
                not isinstance(record['tool'], str) or not isinstance(record['content'], str)):
            raise ValueError('Malformed artifact snapshot.')
        return record

    def read(self, artifact_id, offset=0, max_chars=6000, *, redactor=None):
        if not isinstance(artifact_id, str) or not re.fullmatch(r'[0-9a-f]{64}', artifact_id):
            raise ValueError('Invalid artifact ID.')
        if type(offset) is not int or offset < 0 or type(max_chars) is not int or not 1 <= max_chars <= 6000:
            raise ValueError('Use a nonnegative character offset and 1–6000 max_chars.')
        record = self.record(artifact_id)
        content = record['content']
        if redactor is not None:
            content = redactor.text(content)
        if not isinstance(content, str) or offset > len(content):
            raise ValueError('Invalid artifact content or offset.')
        source_changed = None
        if record['tool'] == 'read_file' and self.workspace is not None:
            try:
                source = json.loads(content)
                path = self.workspace.resolve(source['path'])
                data, _ = self.workspace._read(path)
                source_changed = hashlib.sha256(data).hexdigest() != source['sha256']
            except (ValueError, OSError, KeyError, TypeError):
                source_changed = True
        end = min(len(content), offset + max_chars)
        return {'artifact_id': artifact_id, 'source_tool': record['tool'], 'offset': offset,
                'content': content[offset:end], 'total_characters': len(content),
                'next_offset': end if end < len(content) else None, 'source_changed': source_changed,
                'notice': 'Historical tool snapshot. Re-read current project files before editing; artifact reads do not refresh file read permissions.'}

    def offload(self, tool, content):
        if len(content) <= THRESHOLD or tool in ('read_artifact', 'update_plan'):
            return content
        result = {'source_tool': tool, 'truncated': True, 'characters': len(content),
                  'preview': content[:2500] + '\n…\n' + content[-1000:]}
        try:
            data = json.loads(content)
            if isinstance(data, dict):
                result['metadata'] = {key: value for key, value in data.items()
                                      if key in ('status', 'exit_code', 'passed', 'path', 'sha256', 'error', 'next_page')
                                      and len(json.dumps(value)) <= 1000}
        except ValueError:
            pass
        try:
            result['artifact_id'] = self.put(tool, content)
            result['notice'] = 'Full redacted output saved locally. Use read_artifact to page it; preview is incomplete.'
        except (OSError, ValueError):
            result['artifact_error'] = 'Artifact storage unavailable or quota exceeded; only this excerpt is retained. Inspect storage before repeating any side effect.'
        return json.dumps(result, ensure_ascii=False)


def register_artifact_tool(registry, store, agent=None):
    from .registry import ToolSpec
    registry.register(ToolSpec('read_artifact', 'Read a bounded page from an immutable local tool-output snapshot. This is historical data, not a fresh project-file read.',
        {'type': 'object', 'properties': {'artifact_id': {'type': 'string'},
         'offset': {'type': 'integer', 'minimum': 0}, 'max_chars': {'type': 'integer', 'minimum': 1, 'maximum': 6000}},
         'required': ['artifact_id'], 'additionalProperties': False},
        lambda **args: store.read(**args), effect='read'))

    from .result_projection import register_projection_tool
    register_projection_tool(registry, store, agent)
