"""Bounded net file-tool checkpoints. Not a filesystem transaction or shell rollback."""
import base64
import json
import os
import re
import tempfile
import uuid
from pathlib import Path
from .file_lifecycle import snapshot, digest, identity, parent_fd, unchanged, remove_guarded
from .workspace import preview_change

MAX_PATHS = 32
MAX_BEFORE_BYTES = 8_000_000
MAX_RECORD_BYTES = 24_000_000


def state(current):
    if current is None:
        return None
    return {'sha256': digest(current['data']), 'size': len(current['data']),
            'mode': current['mode'], 'identity': list(current['identity'])}


def packed(current):
    return None if current is None else {**state(current), 'data': base64.b64encode(current['data']).decode('ascii')}


def unpacked(saved):
    if saved is None:
        return None
    data = base64.b64decode(saved['data'], validate=True)
    if len(data) > 2_000_000 or digest(data) != saved['sha256'] or len(data) != saved['size']:
        raise ValueError('Invalid changeset before-image.')
    return {'data': data, 'mode': saved['mode'], 'identity': tuple(saved['identity'])}


def matches(current, expected, *, owner=None):
    if current is None or expected is None:
        return current is None and expected is None
    actual = state(current)
    return (all(actual[key] == expected[key] for key in ('sha256', 'size', 'mode')) and
            (actual['identity'][:2] == owner if owner else actual['identity'] == expected.get('identity')))


class ChangesetStore:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.directory = self.root / '.codine' / 'changesets'
        if (self.root/'.codine').is_symlink() or self.directory.is_symlink():
            raise ValueError('Changeset history cannot be linked.')

    def path(self, ident):
        if not isinstance(ident, str) or not re.fullmatch('[a-f0-9]{32}', ident):
            raise ValueError('Invalid changeset ID.')
        path = self.directory / (ident + '.json')
        if path.is_symlink():
            raise ValueError('Changeset record cannot be linked.')
        return path

    def save(self, record):
        self.__init__(self.root)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        target = self.path(record['id'])
        raw = json.dumps(record, ensure_ascii=False).encode()
        if len(raw) > MAX_RECORD_BYTES:
            raise ValueError('Changeset exceeds its record byte limit.')
        fd, temporary = tempfile.mkstemp(prefix='.set-', dir=self.directory)
        try:
            with os.fdopen(fd, 'wb') as handle:
                handle.write(raw); handle.flush(); os.fsync(handle.fileno())
            os.replace(temporary, target)
            fd = os.open(self.directory, os.O_RDONLY)
            try: os.fsync(fd)
            finally: os.close(fd)
        finally:
            if os.path.exists(temporary): os.unlink(temporary)

    def load(self, ident):
        try:
            return self._load(ident)
        except (KeyError, TypeError, AttributeError, UnicodeError) as error:
            raise ValueError('Malformed changeset record; no recovery attempted.') from error

    def _load(self, ident):
        path = self.path(ident)
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as handle:
            info = os.fstat(handle.fileno())
            import stat
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_RECORD_BYTES:
                raise ValueError('Unsafe changeset record.')
            raw = handle.read(MAX_RECORD_BYTES + 1)
        if len(raw) > MAX_RECORD_BYTES:
            raise ValueError('Oversized changeset record.')
        record = json.loads(raw)
        if (not isinstance(record, dict) or record.get('version') != 1 or record.get('id') != ident or
                record.get('status') not in ('recorded', 'pending', 'rewinding', 'rewound') or
                not isinstance(record.get('entries'), dict) or len(record['entries']) > MAX_PATHS):
            raise ValueError('Invalid changeset record.')
        total = 0
        for path, entry in record['entries'].items():
            if not isinstance(path, str) or not isinstance(entry, dict) or entry.get('rewind') not in ('pending', 'restoring', 'restored'):
                raise ValueError('Invalid changeset path state.')
            before = unpacked(entry['before'])
            total += len(before['data']) if before else 0
            validate_state(entry['expected'])
            if before: validate_state(entry['before'])
            owner = entry.get('restore_owner')
            if owner is not None: validate_owner(owner)
        if total > MAX_BEFORE_BYTES:
            raise ValueError('Changeset before-images exceed 8 MB.')
        pending = record.get('pending')
        if pending is not None:
            if not isinstance(pending, dict) or not pending or set(pending) - set(record['entries']):
                raise ValueError('Invalid changeset pending operation.')
            for item in pending.values():
                if not isinstance(item, dict): raise ValueError('Invalid pending state.')
                validate_state(item['before'])
                validate_state(item['after'], require_identity=False)
                if item.get('owner') is not None: validate_owner(item['owner'])
                if item.get('partial') is not None:
                    payload = base64.b64decode(item['partial'], validate=True)
                    if len(payload) > 2_000_000 or not item['after'] or digest(payload) != item['after']['sha256']:
                        raise ValueError('Invalid pending copy payload.')
        return record

    def list(self):
        if not self.directory.exists(): return []
        paths = sorted(self.directory.glob('*.json'), key=lambda path: path.stat().st_mtime, reverse=True)[:100]
        return [self.load(path.stem) for path in paths]


def validate_owner(owner):
    if not isinstance(owner, list) or len(owner) != 2 or any(type(n) is not int or n < 0 for n in owner):
        raise ValueError('Invalid checkpoint-owned file identity.')


def validate_state(value, require_identity=True):
    if value is None: return
    if (not isinstance(value, dict) or not isinstance(value.get('sha256'), str) or
            not re.fullmatch('[a-f0-9]{64}', value['sha256']) or type(value.get('size')) is not int or
            not 0 <= value['size'] <= 2_000_000 or type(value.get('mode')) is not int or not 0 <= value['mode'] <= 0o7777):
        raise ValueError('Invalid checkpoint file state.')
    if require_identity:
        ident = value.get('identity')
        if not isinstance(ident, list) or len(ident) != 6 or any(type(n) is not int or n < 0 for n in ident):
            raise ValueError('Invalid checkpoint file version.')


def group_id(workspace):
    if not getattr(workspace, 'change_group_id', None):
        workspace.change_group_id = uuid.uuid4().hex
    return workspace.change_group_id


def prepare(workspace, targets, *, partial=False):
    """targets: path -> (observed before snapshot, intended after bytes/mode or None)."""
    if getattr(workspace, '_rewinding', False): return
    targets = {workspace.resolve(path, operation='write').relative_to(workspace.root).as_posix(): values for path, values in targets.items()}
    store = ChangesetStore(workspace.root)
    ident = group_id(workspace)
    record = store.load(ident) if store.path(ident).exists() else {
        'id': ident, 'version': 1, 'session_id': getattr(workspace, 'change_session_id', None),
        'status': 'recorded', 'entries': {}, 'pending': None}
    if record['pending'] or record['status'] in ('rewinding', 'rewound'):
        raise ValueError(f'Changeset {ident} needs recovery before further writes. Use /changeset and /rewind.')
    for path, (before, after) in targets.items():
        workspace.resolve(path, operation='write')
        if path in record['entries']:
            if not matches(before, record['entries'][path]['expected']):
                raise ValueError('File changed outside this turn; refusing to merge unrelated work into its checkpoint.')
        else:
            record['entries'][path] = {'before': packed(before), 'expected': state(before), 'rewind': 'pending'}
    if len(record['entries']) > MAX_PATHS or sum(entry['before']['size'] if entry['before'] else 0 for entry in record['entries'].values()) > MAX_BEFORE_BYTES:
        raise ValueError('Turn checkpoint limit: at most 32 paths and 8 MB of original data. Start a new turn.')
    record['pending'] = {}
    for path, (before, after) in targets.items():
        intended = None if after is None else {'sha256': digest(after['data']), 'size': len(after['data']), 'mode': after['mode']}
        record['pending'][path] = {'before': state(before), 'after': intended, 'owner': None,
            'partial': base64.b64encode(after['data']).decode() if partial and after else None}
    record['status'] = 'pending'
    store.save(record)
    workspace.emit(f'Changeset: {ident} (review /changeset; recover /rewind).')


def own(workspace, path, owner):
    if getattr(workspace, '_rewinding', False) or not getattr(workspace, 'change_group_id', None): return
    store = ChangesetStore(workspace.root)
    if not store.path(workspace.change_group_id).exists(): return
    record = store.load(workspace.change_group_id)
    path = workspace.resolve(path, operation='write').relative_to(workspace.root).as_posix()
    if record['pending'] and path in record['pending']:
        record['pending'][path]['owner'] = list(owner)
        store.save(record)


def complete(workspace):
    if getattr(workspace, '_rewinding', False): return
    store = ChangesetStore(workspace.root)
    record = store.load(group_id(workspace))
    for path, item in record['pending'].items():
        current = snapshot(workspace, path, optional=True, authorize=False)
        if not matches(current, item['after'], owner=item['owner']):
            raise ValueError('Mutation did not match its journaled final state; inspect /rewind.')
        record['entries'][path]['expected'] = state(current)
    record['pending'] = None
    record['status'] = 'recorded'
    store.save(record)


def pending_matches(current, item):
    if matches(current, item['before']): return True
    if item['after'] is None: return current is None
    owner = item.get('owner')
    if owner is None or current is None or list(current['identity'][:2]) != owner: return False
    if matches(current, item['after'], owner=owner): return True
    return bool(item.get('partial') is not None and
                base64.b64decode(item['partial'], validate=True).startswith(current['data']))


def preflight(workspace, record):
    observed = {}
    for path, entry in record['entries'].items():
        workspace.resolve(path, operation='write')
        current = snapshot(workspace, path, optional=True)
        if entry['rewind'] == 'restored':
            valid = matches(current, entry['expected'])
        elif entry['rewind'] == 'restoring':
            valid = matches(current, entry['expected']) or matches(current, entry['before'], owner=entry.get('restore_owner'))
        elif record['pending'] and path in record['pending']:
            valid = pending_matches(current, record['pending'][path])
        else:
            valid = matches(current, entry['expected'])
        if not valid:
            raise ValueError(f'Changeset conflict at {path}; no files changed. Inspect newer work before rewinding.')
        observed[path] = current
    return observed


def diff(path, current, original):
    before = current['data'] if current else b''
    after = original['data'] if original else b''
    try:
        if b'\0' in before + after: raise UnicodeError()
        body = preview_change(path, before.decode(), after.decode())
    except UnicodeError:
        body = 'Binary contents: metadata-only preview.\n'
    def describe(value):
        return 'absent' if value is None else f"{len(value['data'])} bytes sha256:{digest(value['data'])} mode:{oct(value['mode'])}"
    return f'{path}: {describe(current)} -> {describe(original)}\n' + body


def show(workspace, ident=None):
    store = ChangesetStore(workspace.root)
    if not ident:
        return '\n'.join(f"{row['id']}  {row['status']}  {len(row['entries'])} paths  session:{row['session_id']}" for row in store.list()) or 'No turn changesets.'
    record = store.load(ident)
    lines = [f"Changeset {ident}: {record['status']}. File tools only; shell/Git/job effects excluded."]
    for path, entry in record['entries'].items():
        current = snapshot(workspace, path, optional=True)
        lines.append(diff(path, current, unpacked(entry['before'])))
    return '\n'.join(lines)


def restore_one(workspace, store, record, path, current, original):
    unchanged(workspace, path, current)
    entry = record['entries'][path]
    entry['rewind'] = 'restoring'
    entry['expected'] = state(current)
    store.save(record)
    if original is None:
        if current is not None: remove_guarded(workspace, path, current)
    elif current is None or current['data'] != original['data'] or current['mode'] != original['mode']:
        with parent_fd(workspace, path, create=True) as (parent, leaf):
            temporary = '.codine-rewind-' + uuid.uuid4().hex
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
            try:
                with os.fdopen(fd, 'wb') as handle:
                    handle.write(original['data']); handle.flush()
                    os.fchmod(handle.fileno(), original['mode']); os.fsync(handle.fileno())
                    entry['restore_owner'] = list(identity(os.fstat(handle.fileno()))[:2])
                store.save(record)  # Owned replacement journaled before installation.
                unchanged(workspace, path, current)
                if current is None:
                    os.link(temporary, leaf, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
                    os.unlink(temporary, dir_fd=parent)
                else:
                    os.replace(temporary, leaf, src_dir_fd=parent, dst_dir_fd=parent)
                os.fsync(parent)
            finally:
                try: os.unlink(temporary, dir_fd=parent)
                except FileNotFoundError: pass
    final = snapshot(workspace, path, optional=True, authorize=False)
    if original is None and final is not None or original is not None and (final is None or final['data'] != original['data'] or final['mode'] != original['mode']):
        raise ValueError('Restored file changed unexpectedly; checkpoint retained.')
    entry['expected'] = state(final)
    entry['rewind'] = 'restored'
    store.save(record)


def rewind(workspace, ident):
    if workspace.mode == 'plan': raise ValueError('Rewind is disabled in planning mode.')
    store = ChangesetStore(workspace.root)
    record = store.load(ident)
    if record['status'] == 'rewound': return {'status': 'unchanged', 'changeset': ident}
    observed = preflight(workspace, record)
    previews = [diff(path, observed[path], unpacked(entry['before'])) for path, entry in record['entries'].items() if entry['rewind'] != 'restored']
    workspace.emit('\n'.join(previews))
    # All previews and all policy/core approvals precede the first mutation.
    for path, entry in record['entries'].items():
        if entry['rewind'] == 'restored': continue
        if not workspace._confirm(workspace.resolve(path, operation='write'),
                f'Rewind {path} as part of changeset {ident}? Shell/Git/job effects are excluded.', '', '', preview='Preview shown in the combined changeset above.'):
            return {'status': 'declined', 'changeset': ident}
    for path, current in observed.items(): unchanged(workspace, path, current)
    record['status'] = 'rewinding'
    # Pending forward operation resolved to the actual, fully preflighted state.
    for path, current in observed.items(): record['entries'][path]['expected'] = state(current)
    record['pending'] = None
    store.save(record)
    for path, entry in record['entries'].items():
        if entry['rewind'] == 'restored': continue
        restore_one(workspace, store, record, path, observed[path], unpacked(entry['before']))
    record['status'] = 'rewound'
    store.save(record)
    workspace.read_versions.clear(); workspace.read_ranges.clear(); workspace.lifecycle_versions.clear(); getattr(workspace, 'notebook_reads', {}).clear()
    if getattr(workspace, 'change_group_id', None) == ident: workspace.change_group_id = None
    return {'status': 'rewound', 'changeset': ident, 'paths': list(record['entries'])}


def apply(workspace, changes):
    if workspace.mode == 'plan': raise ValueError('Changesets are disabled in planning mode.')
    if not isinstance(changes, list) or not 1 <= len(changes) <= MAX_PATHS:
        raise ValueError('Provide 1–32 text changes.')
    observed, desired = {}, {}
    for change in changes:
        if not isinstance(change, dict) or set(change) != {'path', 'content', 'expected_sha256'}:
            raise ValueError('Each change requires path, content and expected_sha256 (null for a new file).')
        supplied, content = change['path'], change['content']
        path = workspace.resolve(supplied, operation='write').relative_to(workspace.root).as_posix()
        if path in observed: raise ValueError('Each canonical changeset path must appear once.')
        if not isinstance(content, str) or '\0' in content or len(content.encode()) > 2_000_000:
            raise ValueError('Each content must be text under 2 MB.')
        current = snapshot(workspace, path, optional=True)
        if current is None:
            if change['expected_sha256'] is not None: raise ValueError('New files require null expected_sha256.')
        else:
            original = current['data'].decode('utf-8')
            if (change['expected_sha256'] != digest(current['data']) or
                    workspace.read_versions.get(supplied) != change['expected_sha256']):
                raise ValueError('Read every current file and supply its SHA256 before grouped edits.')
            covered = 0
            for left, right in sorted(workspace.read_ranges.get(supplied, [])):
                if left <= covered: covered = max(covered, right)
            if covered < len(original): raise ValueError('Read each complete file before whole-file grouped replacement.')
        observed[path] = current
        desired[path] = {'data': content.encode(), 'mode': current['mode'] & 0o777 if current else 0o644}
    if sum(len(item['data']) for item in desired.values()) > MAX_BEFORE_BYTES:
        raise ValueError('Combined replacement data exceeds 8 MB.')
    preview = '\n'.join(diff(path, observed[path], after) for path, after in desired.items())
    if len(preview.encode()) > 256000:
        raise ValueError('Combined preview exceeds 256 KB; split the changeset.')
    workspace.emit(preview)
    for path in observed:
        if not workspace._confirm(workspace.resolve(path, operation='write'), f'Apply grouped edit to {path}?', '', '', preview='Preview shown in combined changeset above.'):
            return {'status': 'declined'}
    for path, current in observed.items(): unchanged(workspace, path, current)
    # Reserve originals for all paths before the first write, including later paths
    # never reached if applying the set is interrupted.
    store = ChangesetStore(workspace.root)
    ident = group_id(workspace)
    record = store.load(ident) if store.path(ident).exists() else {
        'id': ident, 'version': 1, 'session_id': getattr(workspace, 'change_session_id', None),
        'status': 'recorded', 'entries': {}, 'pending': None}
    if record['pending'] or record['status'] in ('rewinding', 'rewound'):
        raise ValueError('Recover the pending changeset before applying more changes.')
    for path, current in observed.items():
        if path in record['entries']:
            if not matches(current, record['entries'][path]['expected']): raise ValueError('File changed outside this turn.')
        else:
            record['entries'][path] = {'before': packed(current), 'expected': state(current), 'rewind': 'pending'}
    if len(record['entries']) > MAX_PATHS or sum(item['before']['size'] if item['before'] else 0 for item in record['entries'].values()) > MAX_BEFORE_BYTES:
        raise ValueError('Turn checkpoint exceeds 32 paths or 8 MB originals.')
    store.save(record)
    tracked = workspace.track_changes
    workspace.track_changes = True
    try:
        for path, after in desired.items():
            current = observed[path]
            workspace._atomic_write(workspace.resolve(path, operation='write'), after['data'], current['data'] if current else None)
    finally:
        workspace.track_changes = tracked
        workspace.read_versions.clear(); workspace.read_ranges.clear(); workspace.lifecycle_versions.clear(); getattr(workspace, 'notebook_reads', {}).clear()
    return {'status': 'applied', 'changeset': ident, 'paths': list(observed)}
