"""Guarded regular-file lifecycle operations with durable recovery before-images."""
import base64
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import stat

LIMIT = 2_000_000


def digest(data):
    return hashlib.sha256(data).hexdigest()


@contextmanager
def parent_fd(workspace, name, create=False):
    parts = Path(name).parts
    descriptor = os.open(workspace.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            if create:
                try: os.mkdir(part, mode=0o755, dir_fd=descriptor)
                except FileExistsError: pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        yield descriptor, parts[-1]
    finally:
        os.close(descriptor)


def identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_nlink)


def snapshot(workspace, name, *, optional=False, authorize=True):
    target = workspace.resolve(name)
    if authorize:
        workspace._authorize_read(target)
    try:
        with parent_fd(workspace, name) as (parent, leaf):
            entry = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
            if not stat.S_ISREG(entry.st_mode) or entry.st_nlink != 1 or entry.st_size > LIMIT:
                raise ValueError('Lifecycle operations require a single-link regular file no larger than 2 MB.')
            descriptor = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            try:
                before = os.fstat(descriptor)
                if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > LIMIT:
                    raise ValueError('Lifecycle operations require a single-link regular file no larger than 2 MB.')
                data = bytearray()
                while len(data) <= LIMIT:
                    piece = os.read(descriptor, min(65536, LIMIT + 1 - len(data)))
                    if not piece: break
                    data.extend(piece)
                if len(data) > LIMIT or identity(before) != identity(os.fstat(descriptor)):
                    raise ValueError('File changed while inspecting lifecycle state.')
                return {'data': bytes(data), 'identity': identity(before), 'mode': stat.S_IMODE(before.st_mode)}
            finally:
                os.close(descriptor)
    except FileNotFoundError:
        if optional: return None
        raise


def metadata(name, current):
    data = current['data']
    try:
        data.decode('utf-8')
        kind = 'binary' if b'\0' in data else 'text'
    except UnicodeError:
        kind = 'binary'
    return {'path': name, 'size': len(data), 'sha256': digest(data), 'mode': oct(current['mode']), 'kind': kind}


def inspect_file(workspace, path):
    current = snapshot(workspace, path)
    if not hasattr(workspace, 'lifecycle_versions'): workspace.lifecycle_versions = {}
    workspace.lifecycle_versions[path] = current['identity'], digest(current['data'])
    return metadata(path, current)


def require_inspected(workspace, path, current, expected):
    version = digest(current['data'])
    inspected = getattr(workspace, 'lifecycle_versions', {}).get(path)
    if inspected == (current['identity'], version) and expected == version:
        return
    # A complete normal text read also establishes lifecycle intent.
    try: length = len(current['data'].decode('utf-8'))
    except UnicodeError: length = None
    covered = 0
    for left, right in sorted(workspace.read_ranges.get(path, [])):
        if left <= covered: covered = max(covered, right)
    if (length is not None and b'\0' not in current['data'] and covered >= length and
            workspace.read_versions.get(path) == version and expected == version):
        return
    raise ValueError('Inspect this file or read all current text first, then supply its current SHA256.')


def preview(workspace, path, current, remove):
    info = metadata(path, current)
    if info['kind'] == 'binary':
        return f"{'Remove' if remove else 'Restore/create'} binary {path}: {info['size']} bytes, SHA256 {info['sha256']}, mode {info['mode']}"
    from .workspace import preview_change
    text = current['data'].decode('utf-8')
    diff = preview_change(path, text if remove else '', '' if remove else text)
    if len(diff) > 24000: diff = diff[:24000] + '\n[Preview truncated; SHA256 identifies the complete file.]'
    return f"{path}: {info['size']} bytes, SHA256 {info['sha256']}, mode {info['mode']}\n" + diff


def confirm(workspace, path, current, description, remove):
    target = workspace.resolve(path, operation='write')
    return workspace._confirm(target, description, '', '', preview=preview(workspace, path, current, remove))


def unchanged(workspace, path, expected):
    workspace.resolve(path, operation='write')
    current = snapshot(workspace, path, optional=True, authorize=False)
    if expected is None:
        if current is not None: raise ValueError('Destination appeared after preview; refusing to overwrite.')
    elif current is None or current['identity'] != expected['identity'] or current['data'] != expected['data']:
        raise ValueError('File changed after preview; inspect it again.')
    return current


def remove_guarded(workspace, path, expected):
    unchanged(workspace, path, expected)
    with parent_fd(workspace, path) as (parent, leaf):
        info = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
        if identity(info) != expected['identity']:
            raise ValueError('File identity changed before removal.')
        os.unlink(leaf, dir_fd=parent)
        os.fsync(parent)


def create_exclusive(workspace, path, data, mode, store, record, identity_key):
    workspace.resolve(path, operation='write')
    with parent_fd(workspace, path, create=True) as (parent, leaf):
        descriptor = os.open(leaf, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode & 0o7777, dir_fd=parent)
        initial = os.fstat(descriptor)
        try:
            record[identity_key] = [initial.st_dev, initial.st_ino]
            store.save(record)  # Ownership is durable before any copy bytes are written.
            view = memoryview(data)
            while view:
                written = os.write(descriptor, view)
                if written <= 0: raise OSError('Short lifecycle write.')
                view = view[written:]
            os.fchmod(descriptor, mode & 0o7777)
            os.fsync(descriptor)
        except BaseException:
            # If ownership journaling failed, remove only our unchanged empty inode.
            current = os.fstat(descriptor)
            if current.st_size == 0 and identity(current) == identity(initial):
                try:
                    if identity(os.stat(leaf, dir_fd=parent, follow_symlinks=False)) == identity(initial):
                        os.unlink(leaf, dir_fd=parent)
                        os.fsync(parent)
                except OSError: pass
            raise
        finally:
            os.close(descriptor)
        os.fsync(parent)


def operate(workspace, path, expected_sha256, destination=None):
    if workspace.mode == 'plan': raise ValueError('File lifecycle changes are disabled in planning mode.')
    source = workspace.resolve(path, operation='write')
    if destination is not None:
        target = workspace.resolve(destination, operation='write')
        if source == target: raise ValueError('Source and destination must differ.')
        if target.exists(): raise ValueError('Destination exists; lifecycle tools never overwrite files.')
    current = snapshot(workspace, path)
    require_inspected(workspace, path, current, expected_sha256)
    operation = 'move' if destination is not None else 'delete'
    if not confirm(workspace, path, current, f'{operation.title()} {path}' + (f' to {destination}?' if destination else '?'), True):
        return {'status': 'declined', 'path': path}
    if destination is not None and not confirm(workspace, destination, current, f'Create destination {destination} for move?', False):
        return {'status': 'declined', 'path': path}
    unchanged(workspace, path, current)
    if destination is not None: unchanged(workspace, destination, None)
    from .changes import ChangeStore
    store = ChangeStore(workspace.root)
    record = store.prepare_lifecycle(operation, path, current['data'], current['mode'], destination,
                                     session_id=getattr(workspace, 'change_session_id', None))
    workspace.emit(f"Recovery checkpoint: {record['id']} (use /diff or /undo with this ID).")
    if destination is not None:
        create_exclusive(workspace, destination, current['data'], current['mode'], store, record, 'destination_identity')
        copied = snapshot(workspace, destination, authorize=False)
        record['destination_stamp'] = list(copied['identity'])
        record['phase'] = 'copied'
        store.save(record)
        unchanged(workspace, destination, copied)
        if copied['data'] != current['data'] or not owned(copied, record.get('destination_identity')):
            raise ValueError('Destination changed during move; source was retained.')
    remove_guarded(workspace, path, current)
    record['phase'] = 'completed'
    store.save(record)
    workspace.read_versions.pop(path, None)
    workspace.read_ranges.pop(path, None)
    getattr(workspace, 'lifecycle_versions', {}).pop(path, None)
    return {'status': 'moved' if destination else 'deleted', 'path': path, 'destination': destination,
            'change_id': record['id'], 'sha256': digest(current['data'])}


def load_bytes(record):
    return base64.b64decode(record['before_b64'], validate=True)


def owned(current, recorded):
    return current is not None and recorded == list(current['identity'][:2])


def show(workspace, record):
    workspace._authorize_read(workspace.resolve(record['path']))
    if record.get('destination'):
        workspace._authorize_read(workspace.resolve(record['destination']))
    original = {'data': load_bytes(record), 'mode': record['mode']}
    return f"Lifecycle checkpoint {record['id']}: {record['operation']} {record['path']} -> {record.get('destination')} ({record['phase']})\n" + preview(workspace, record['path'], original, True)


def undo(workspace, store, record):
    path, destination = record['path'], record.get('destination')
    workspace.resolve(path, operation='write')
    if destination: workspace.resolve(destination, operation='write')
    original = {'data': load_bytes(record), 'mode': record['mode']}
    source = snapshot(workspace, path, optional=True)
    target = snapshot(workspace, destination, optional=True) if destination else None
    partial_restore = source is not None and source['data'] != original['data']
    if partial_restore and not (owned(source, record.get('restore_identity')) and original['data'].startswith(source['data'])):
        raise ValueError('Source changed since checkpoint; refusing to overwrite newer work.')
    if target is not None:
        if not owned(target, record.get('destination_identity')) or not original['data'].startswith(target['data']):
            raise ValueError('Destination changed since checkpoint; refusing recovery over newer work.')
        if record.get('destination_stamp') and list(target['identity']) != record['destination_stamp']:
            raise ValueError('Destination version changed since checkpoint.')
        if record['phase'] != 'prepared' and (target['data'] != original['data'] or target['mode'] != original['mode']):
            raise ValueError('Completed destination changed since checkpoint.')
    if not confirm(workspace, path, original, f'Restore {path} from lifecycle checkpoint?', False):
        return {'status': 'declined'}
    if target is not None and not confirm(workspace, destination, target, f'Remove checkpoint-owned destination {destination}?', True):
        return {'status': 'declined'}
    unchanged(workspace, path, source)
    if destination: unchanged(workspace, destination, target)
    if partial_restore:
        remove_guarded(workspace, path, source)
        source = None
    if source is None:
        create_exclusive(workspace, path, original['data'], original['mode'], store, record, 'restore_identity')
    if target is not None:
        remove_guarded(workspace, destination, target)
    record['undone'] = True
    store.save(record)
    workspace.read_versions.pop(path, None)
    workspace.read_ranges.pop(path, None)
    return {'status': 'undone', 'change_id': record['id'], 'path': path, 'destination': destination}
