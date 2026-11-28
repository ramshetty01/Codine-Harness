"""Metadata-only snapshots of explicitly declared repository extensions."""
import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat

LIMIT = 1024 * 1024
MAX_FILES = 32


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def relative_path(value):
    if (not isinstance(value, str) or not value or len(value) > 1024 or
            '\\' in value or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ValueError('Invalid extension file path.')
    path = PurePosixPath(value)
    if path.is_absolute() or '..' in path.parts or str(path) != value:
        raise ValueError('Extension paths must be normalized relative paths.')
    return value


def read_bytes(root, name, limit):
    """Descriptor traversal; no links, special files or unbounded reads."""
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    fd = None
    try:
        parts = Path(relative_path(name)).parts
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > limit:
            raise ValueError('Extension snapshot requires bounded regular files with one link.')
        data = bytearray()
        while len(data) <= limit:
            chunk = os.read(fd, min(65536, limit + 1 - len(data)))
            if not chunk: break
            data.extend(chunk)
        after = os.fstat(fd)
        version = lambda st: (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns, st.st_mode, st.st_nlink)
        if len(data) > limit or version(before) != version(after):
            raise ValueError('Extension file changed during review or exceeds size limit.')
        return bytes(data), [before.st_dev, before.st_ino]
    finally:
        if fd is not None: os.close(fd)
        os.close(directory)


def snapshot_extension(workspace, manifest_path):
    name = relative_path(manifest_path)
    target = workspace.resolve(name)
    workspace._authorize_read(target)
    raw, identity = read_bytes(workspace.root, name, min(LIMIT, 65536))
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError('Duplicate extension manifest field.')
            value[key] = item
        return value
    def invalid_constant(_):
        raise ValueError('Extension manifest requires finite JSON values.')
    try:
        manifest = json.loads(raw, object_pairs_hook=unique, parse_constant=invalid_constant)
    except (UnicodeError, RecursionError) as error:
        raise ValueError('Extension manifest must be bounded UTF-8 JSON without excessive nesting.') from error
    if (not isinstance(manifest, dict) or set(manifest) != {'version', 'name', 'entrypoint', 'files', 'capabilities'} or
            type(manifest['version']) is not int or manifest['version'] != 1):
        raise ValueError('Expected extension manifest version 1 with name, entrypoint, files and capabilities.')
    if (not isinstance(manifest['name'], str) or not 1 <= len(manifest['name']) <= 80 or
            any(not (c.isascii() and (c.isalnum() or c in '-_.')) for c in manifest['name'])):
        raise ValueError('Extension name must use ASCII letters, digits, dot, dash or underscore.')
    files = manifest['files']
    if not isinstance(files, list) or not 1 <= len(files) <= MAX_FILES:
        raise ValueError('Extension must declare 1 to 32 files.')
    files = [relative_path(path) for path in files]
    if len(set(files)) != len(files): raise ValueError('Duplicate extension files.')
    entrypoint = relative_path(manifest['entrypoint'])
    if entrypoint not in files or not entrypoint.endswith('.py'):
        raise ValueError('Python entrypoint must be a declared .py file.')
    caps = manifest['capabilities']
    if (not isinstance(caps, list) or len(caps) > 32 or
            any(not isinstance(c, str) or not 1 <= len(c) <= 80 or
                any(not (x.isascii() and (x.isalnum() or x in '-_.:')) for x in c) for c in caps) or
            len(set(caps)) != len(caps) or not set(caps) <= {'tools', 'commands', 'events', 'ui'}):
        raise ValueError('Capabilities must be distinct bounded ASCII labels.')
    rows, total = [], len(raw)
    for path in sorted(files):
        project_path = (Path(name).parent / path).as_posix()
        target = workspace.resolve(project_path)
        workspace._authorize_read(target)
        data, file_identity = read_bytes(workspace.root, project_path, LIMIT - total)
        total += len(data)
        rows.append({'path': path, 'identity': file_identity, 'sha256': hashlib.sha256(data).hexdigest(),
                     'content_base64': base64.b64encode(data).decode('ascii')})
    root_stat = workspace.root.stat()
    snapshot = {'version': 1, 'project': str(workspace.root),
                'project_identity': [root_stat.st_dev, root_stat.st_ino], 'manifest_path': name,
                'manifest_identity': identity, 'manifest_sha256': hashlib.sha256(raw).hexdigest(),
                'manifest': manifest, 'files': rows}
    snapshot['digest'] = hashlib.sha256(canonical(snapshot)).hexdigest()
    return snapshot
