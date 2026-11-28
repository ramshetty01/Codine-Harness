"""Exact-source editing trees with explicit conflict-aware integration (not Git branches)."""
from copy import deepcopy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import uuid

from .changesets import apply, diff, state, matches
from .file_lifecycle import snapshot
from .input_settings import directory
from .paths import STATE_ROOT
from .staging import StagingLimits, _validate_path, _read_file
from .workspace import Workspace

TREE_ROOT = STATE_ROOT / 'editing-trees'
LIMITS = StagingLimits(max_files=32, max_file_bytes=1_000_000, max_total_bytes=8_000_000)


def canonical(value): return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
def digest(value): return hashlib.sha256(canonical(value)).hexdigest()


class _MappedPolicy:
    def __init__(self, policy, project):
        self.policy, self.project = deepcopy(policy), str(project)
        self.source, self.file_rules = policy.source, policy.file_rules
    def decide_file(self, **arguments):
        arguments['project'] = self.project
        return self.policy.decide_file(**arguments)


class IsolatedTrees:
    """Host-only API: caller/supervisor retains all provider and budget authority."""
    def __init__(self, workspace, root=None):
        self.parent = workspace
        self.root = Path(root if root is not None else TREE_ROOT).absolute()
        workspace.protected_roots.add(self.root.resolve())
        self.reviews = {}

    def _ident(self, ident):
        if not isinstance(ident, str) or not re.fullmatch('[0-9a-f]{32}', ident):
            raise ValueError('Invalid isolated tree ID.')
        return self.root / ident

    def _load(self, ident):
        location = self._ident(ident)
        with directory(location) as fd:
            info = os.fstat(fd)
            if stat.S_IMODE(info.st_mode) != 0o700: raise ValueError('Tree receipt directory must be private.')
            def private(info, kind):
                if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != (0o600 if kind == 'file' else 0o700):
                    raise ValueError('Isolated tree receipts must be private and user-owned.')
            raw = _read_file(fd, ('base.json',), 65536, validate=private)
            try: record = json.loads(raw)
            except (ValueError, UnicodeError): raise ValueError('Invalid isolated tree receipt.') from None
            tree = os.open('tree', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(tree)
        if (not isinstance(record, dict) or type(record.get('version')) is not int or record.get('version') != 1 or record.get('id') != ident
                or record.get('project') != str(self.parent.root) or not isinstance(record.get('files'), dict)
                or not 1 <= len(record['files']) <= 32):
            raise ValueError('Isolated tree receipt has another project or invalid scope.')
        info = self.parent.root.stat()
        if record.get('project_identity') != [info.st_dev, info.st_ino]: raise ValueError('Parent project directory changed.')
        for name, base in record['files'].items():
            _validate_path(name, LIMITS)
            from .changesets import validate_state
            validate_state(base)
        return record

    def _workspace(self, root, *, mapped=False, approve=None):
        result = Workspace(root, approve or self.parent.approve, self.parent.emit, self.parent.app_root)
        result.mode = self.parent.mode
        result.protected_roots.update(self.parent.protected_roots)
        if mapped:
            # Grant only this already validated subtree; sibling receipts cannot
            # be reached through project-relative paths. No parent-root widening.
            result.protected_roots = {path for path in result.protected_roots if not root.is_relative_to(path)}
        result.protected_paths.update(self.parent.protected_paths)
        policy = getattr(self.parent, 'command_policy', None)
        if policy is not None: result.command_policy = _MappedPolicy(policy, self.parent.root) if mapped else deepcopy(policy)
        result.policy_interactive = getattr(self.parent, 'policy_interactive', False)
        return result

    def workspace(self, ident):
        self._load(ident)
        return self._workspace(self._ident(ident)/'tree', mapped=True)

    def allocate(self, paths):
        if not isinstance(paths, list) or not 1 <= len(paths) <= 32: raise ValueError('Select 1–32 explicit source files.')
        files = {}; total = 0; seen = set()
        import unicodedata
        for name in paths:
            _validate_path(name, LIMITS)
            folded = unicodedata.normalize('NFC', name).casefold()
            if folded in seen: raise ValueError('Duplicate or case-colliding source path.')
            seen.add(folded)
            current = snapshot(self.parent, name)
            if b'\0' in current['data']: raise ValueError('Snapshot editing currently supports UTF-8 text only.')
            current['data'].decode('utf-8')
            total += len(current['data'])
            if len(current['data']) > LIMITS.max_file_bytes or total > LIMITS.max_total_bytes:
                raise ValueError('Snapshot exceeds per-file/total byte bounds.')
            files[name] = current
        ident = uuid.uuid4().hex
        with directory(self.root, create=True) as rootfd:
            if stat.S_IMODE(os.fstat(rootfd).st_mode) != 0o700: raise ValueError('Editing tree root must have mode 0700.')
            os.mkdir(ident, 0o700, dir_fd=rootfd)
        location = self._ident(ident)
        info = self.parent.root.stat()
        record = {'version': 1, 'id': ident, 'project': str(self.parent.root),
                  'project_identity': [info.st_dev, info.st_ino], 'files': {name:state(value) for name,value in files.items()}}
        # Exclusively created private container, no repository files can choose
        # these metadata names. Materialize bytes directly: never invoke Git.
        with directory(location) as fd:
            os.mkdir('tree', 0o700, dir_fd=fd)
            for name, current in files.items():
                target = location/'tree'/name
                with directory(target.parent, create=True) as parent:
                    handle = os.open(target.name, os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW, current['mode'] & 0o777, dir_fd=parent)
                    with os.fdopen(handle,'wb') as stream:
                        stream.write(current['data']);stream.flush();os.fsync(stream.fileno())
                        os.fchmod(stream.fileno(), current['mode'] & 0o777)
            handle = os.open('base.json',os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600,dir_fd=fd)
            with os.fdopen(handle,'wb') as stream:
                stream.write(canonical(record));stream.flush();os.fsync(stream.fileno())
            os.fsync(fd)
        return {'id': ident, 'path': str(location/'tree'), 'base_digest': digest(record),
                'files': sorted(files), 'kind': 'isolated_source_snapshot',
                'notice': 'No Git branch, credentials or state copied. Integration requires explicit review and approval.'}

    def _proposal(self, ident, new_paths):
        record = self._load(ident)
        if not isinstance(new_paths, list) or len(new_paths) > 32: raise ValueError('New paths must be an explicit bounded list.')
        names = list(record['files'])
        for name in new_paths:
            _validate_path(name, LIMITS)
            if name in names: raise ValueError('New paths must not duplicate base paths.')
            names.append(name)
        if len(names)>32: raise ValueError('Integration supports at most 32 files.')
        import unicodedata
        if len({unicodedata.normalize('NFC',p).casefold() for p in names}) != len(names):
            raise ValueError('Case-colliding integration paths.')
        child = self.workspace(ident)
        changes = []; previews = []; identities = {}; total = 0
        for name in names:
            current = snapshot(child, name, optional=True)
            base = record['files'].get(name)
            if current is None: raise ValueError('Deleted files are unsupported; restore the tree file before review. Deletions are never silently omitted.')
            if b'\0' in current['data']: raise ValueError('Binary integration is unsupported.')
            content = current['data'].decode('utf-8')
            total += len(current['data'])
            if len(current['data']) > LIMITS.max_file_bytes or total > LIMITS.max_total_bytes:
                raise ValueError('Integration byte bounds exceeded.')
            if base and current['mode'] != base['mode']: raise ValueError('File-mode changes are unsupported; restore the original mode.')
            if base and hashlib.sha256(current['data']).hexdigest() == base['sha256']: continue
            self.parent.resolve(name, operation='write')
            parent = snapshot(self.parent, name, optional=True)
            if not matches(parent, base): raise ValueError('Parent conflict at '+name+'; no files were changed.')
            changes.append({'path': name, 'content': content, 'expected_sha256': base['sha256'] if base else None})
            identities[name] = state(current)
            previews.append(diff(name, parent, current))
        preview='\n'.join(previews)
        if len(preview.encode())>256000: raise ValueError('Integration preview exceeds 256 KiB.')
        return {'id':ident,'base_digest':digest(record),'new_paths':new_paths,'changes':changes,
                'child_states':identities,'preview':preview}

    def review(self, ident, *, new_paths=None):
        proposal = self._proposal(ident, new_paths or [])
        token = digest(proposal)
        if len(self.reviews)>=16: self.reviews.pop(next(iter(self.reviews)))
        self.reviews[token]=deepcopy(proposal)
        return {'review':token,'id':ident,'paths':[row['path'] for row in proposal['changes']],
                'preview':proposal['preview'],'status':'reviewed', 'notice':'No parent writes. Explicit integrate(review) still requires file approvals.'}

    def integrate(self, review):
        # Serialize cooperating integrations, without holding the storage lock
        # that individual changeset checkpoints acquire themselves.
        with directory(self.parent.root/'.codine', create=True) as parent:
            handle = os.open('integration.lock', os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW|os.O_NONBLOCK, 0o600, dir_fd=parent)
            try:
                info = os.fstat(handle)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid() or info.st_mode & 0o077:
                    raise ValueError('Unsafe integration lock.')
                try: fcntl.flock(handle, fcntl.LOCK_EX|fcntl.LOCK_NB)
                except BlockingIOError: raise ValueError('Another integration is running; review again after it completes.') from None
                return self._integrate(review)
            finally: os.close(handle)

    def _integrate(self, review):
        if self.parent.mode == 'plan': raise ValueError('Integration is disabled in planning mode.')
        original=self.reviews.get(review)
        if original is None: raise ValueError('Review this exact tree in the current process before integration.')
        def recheck():
            fresh=self._proposal(original['id'], original['new_paths'])
            if digest(fresh)!=review: raise ValueError('Tree or parent changed since review; review again.')
            return fresh
        proposal=recheck()
        if not proposal['changes']: return {'status':'unchanged'}
        def approve(text):
            decision=self.parent.approve(text)
            recheck()  # Approvals cannot silently authorize newer child bytes.
            return decision
        target=self._workspace(self.parent.root, approve=approve)
        # Review/integration has its own read coverage, never authorizes later
        # parent/model edits. apply() still requires full regions and fresh hashes.
        for change in proposal['changes']:
            if change['expected_sha256'] is None: continue
            page={}
            while True:
                result=target.read_file(change['path'],expected_sha256=change['expected_sha256'],**page)
                if result['next_page'] is None: break
                page=result['next_page']
        recheck()
        result=apply(target,proposal['changes'])
        if result.get('status')=='applied': self.reviews.pop(review,None)
        return result
