"""Read-only, bounded Git inspection with an explicit session baseline."""
import hashlib
import json
import os
from pathlib import Path
import selectors
import shutil
import subprocess
import tempfile
import time

from .changes import ChangeStore
from .registry import ToolSpec


class GitError(ValueError):
    pass


def validate_baseline(value):
    if not isinstance(value, dict) or len(json.dumps(value)) > 200000:
        raise ValueError('Invalid Git baseline.')
    if not value:
        return
    if value.get('version') != 1 or type(value.get('repository')) is not bool or type(value.get('captured_ns')) is not int:
        raise ValueError('Invalid Git baseline metadata.')
    entries = value.get('entries', {})
    if not isinstance(entries, dict) or len(entries) > 200 or any(not isinstance(row, dict) for row in entries.values()):
        raise ValueError('Invalid Git baseline entries.')


def git_read(root, args, *, limit=2_000_000, timeout=5):
    git = shutil.which('git', path=os.defpath)
    if not git:
        raise GitError('Git is unavailable.')
    env = {'PATH': os.defpath, 'LC_ALL': 'C', 'GIT_CONFIG_NOSYSTEM': '1',
           'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_SYSTEM': os.devnull,
           'GIT_TERMINAL_PROMPT': '0', 'GIT_OPTIONAL_LOCKS': '0', 'GIT_ATTR_NOSYSTEM': '1'}
    command = [git, '--no-pager', '--no-optional-locks', '--literal-pathspecs',
               '-c', 'core.fsmonitor=false', '-c', 'core.hooksPath=' + os.devnull,
               '-c', 'core.untrackedCache=false', '-c', 'color.ui=false', *args]
    process = subprocess.Popen(command, cwd=root, env=env, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    output, errors = bytearray(), 0
    started, truncated = time.monotonic(), False
    try:
        with selectors.DefaultSelector() as selector:
            for name, pipe in (('stdout', process.stdout), ('stderr', process.stderr)):
                os.set_blocking(pipe.fileno(), False)
                selector.register(pipe, selectors.EVENT_READ, name)
            while selector.get_map() or process.poll() is None:
                if time.monotonic() - started > timeout:
                    raise GitError('Git inspection exceeded its time limit.')
                for key, _ in selector.select(.05):
                    data = os.read(key.fileobj.fileno(), 8192)
                    if not data:
                        selector.unregister(key.fileobj)
                    elif key.data == 'stdout':
                        remaining = limit - len(output)
                        output.extend(data[:remaining])
                        if len(data) > remaining:
                            truncated = True
                            return {'code': None, 'data': bytes(output), 'truncated': True}
                    else:
                        errors += len(data)
                        if errors > 16000:
                            raise GitError('Git emitted oversized diagnostics; contents withheld.')
            return {'code': process.wait(), 'data': bytes(output), 'truncated': truncated}
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        process.stdout.close()
        process.stderr.close()


class GitInspector:
    def __init__(self, workspace, get_session, save=None):
        self.workspace = workspace
        self.get_session = get_session
        self.save = save

    def _metadata(self):
        root = self.workspace.root
        check = git_read(root, ['rev-parse', '--is-inside-work-tree'], limit=1000)
        if check['code'] or check['data'].strip() != b'true':
            return None
        prefix = git_read(root, ['rev-parse', '--show-prefix'], limit=4000)
        head = git_read(root, ['rev-parse', '--verify', 'HEAD'], limit=1000)
        branch = git_read(root, ['symbolic-ref', '--quiet', '--short', 'HEAD'], limit=1000)
        if prefix['code'] or prefix['truncated']:
            raise GitError('Cannot determine Git workspace scope.')
        return {'prefix': prefix['data'].decode().rstrip('\n'),
                'head': head['data'].decode().strip() if head['code'] == 0 else None,
                'branch': branch['data'].decode(errors='replace').strip() if branch['code'] == 0 else '(detached or unborn)'}

    def _entries(self, metadata):
        raw = git_read(self.workspace.root, ['status', '--porcelain=v1', '-z', '--untracked-files=all', '--ignore-submodules=all', '--', '.'])
        if raw['code'] not in (0, None):
            raise GitError('Git status failed; diagnostics withheld.')
        parts = raw['data'].split(b'\0')
        entries, excluded, index = [], 0, 0
        while index < len(parts) - 1:
            item = parts[index]
            index += 1
            if len(item) < 4 or item[2:3] != b' ':
                raise GitError('Malformed Git status output.')
            state, name = item[:2].decode('ascii'), item[3:].decode('utf-8', errors='strict')
            if 'R' in state or 'C' in state:
                index += 1  # Consume original path; only scoped destination is displayed.
            if not name.startswith(metadata['prefix']):
                excluded += 1
                continue
            name = name[len(metadata['prefix']):]
            try:
                path = self.workspace.resolve(name)
                if path.exists() and path.stat().st_nlink != 1:
                    raise ValueError('Linked path')
            except (ValueError, OSError):
                excluded += 1
                continue
            entries.append({'path': name, 'index': state[0], 'worktree': state[1]})
            if len(entries) >= 200:
                return entries, True, excluded
        return entries, raw['truncated'], excluded

    def _fingerprint(self, name):
        try:
            path = self.workspace.resolve(name)
            decision = self.workspace._file_decision('read', path) if hasattr(self.workspace, '_file_decision') else None
            if decision is not None and decision.action != 'allow':
                return None
            data, _ = self.workspace._read(path)
            return hashlib.sha256(data).hexdigest()
        except FileNotFoundError:
            return 'missing'
        except (OSError, ValueError):
            return None

    def capture(self, reason='first_inspection'):
        session = self.get_session()
        if session.git_baseline:
            return
        metadata = self._metadata()
        baseline = {'version': 1, 'captured_ns': time.time_ns(), 'reason': reason, 'repository': metadata is not None}
        if metadata:
            entries, truncated, _ = self._entries(metadata)
            # No source contents enter the baseline. Fingerprints are best-effort
            # and restricted to a small set, never a claim of exclusive ownership.
            baseline.update(head=metadata['head'], branch=metadata['branch'], truncated=truncated,
                            entries={row['path']: {**row, 'sha256': self._fingerprint(row['path']) if i < 32 else None}
                                     for i, row in enumerate(entries)})
        session.git_baseline = baseline
        if self.save:
            try:
                self.save()
            except BaseException:
                session.git_baseline = {}
                raise

    def status(self):
        metadata = self._metadata()
        if metadata is None:
            return {'repository': False, 'notice': 'Project is not inside a Git work tree.'}
        self.capture()
        session = self.get_session()
        entries, truncated, excluded = self._entries(metadata)
        baseline = session.git_baseline
        checkpoints = {}
        try:
            # Bound checkpoint data inspected; unknown provenance remains unknown.
            consumed = 0
            history = ChangeStore(self.workspace.root)
            for path in sorted(history.directory.glob('*.json'), reverse=True)[:64]:
                size = path.stat().st_size
                if consumed + size > 16_000_000:
                    break
                consumed += size
                row = history.load(path.stem)
                if row.get('session_id') == session.id and not row.get('undone'):
                    checkpoints.setdefault(row['path'], row['after_hash'])
        except (OSError, ValueError):
            checkpoints = {}
        for i, row in enumerate(entries):
            previous = baseline.get('entries', {}).get(row['path'])
            current = self._fingerprint(row['path']) if i < 32 else None
            row['pre_existing_at_baseline'] = previous is not None
            row['content_changed_since_baseline'] = (current != previous.get('sha256') if previous and current is not None and previous.get('sha256') is not None else None)
            row['matches_session_file_tool_output'] = current is not None and checkpoints.get(row['path']) == current
        return {'repository': True, 'branch': metadata['branch'], 'head': metadata['head'],
                'head_changed_since_baseline': metadata['head'] != baseline.get('head'),
                'changes': entries, 'truncated': truncated, 'excluded_paths': excluded,
                'baseline_reason': baseline.get('reason'), 'baseline_truncated': baseline.get('truncated', False),
                'notice': 'Baseline records prior work; matching checkpoints identify file-tool output, not exclusive ownership. Shell edits and concurrent changes may be unattributed. No Git mutation was performed.'}

    def diff(self, path, staged=False):
        target = self.workspace.resolve(path)
        path = target.relative_to(self.workspace.root).as_posix()
        metadata = self._metadata()
        if metadata is None:
            raise GitError('Project is not inside a Git work tree.')
        repo_path = metadata['prefix'] + path
        if staged:
            if hasattr(self.workspace, '_authorize_read'):
                self.workspace._authorize_read(target)
        else:
            try:
                after, _ = self.workspace._read(target)
            except FileNotFoundError:
                after = b''
        unmerged = git_read(self.workspace.root, ['ls-files', '--unmerged', '-z', '--', path], limit=16000)
        if unmerged['code'] or unmerged['truncated']:
            raise GitError('Cannot inspect index conflict state.')
        if unmerged['data']:
            raise GitError('Index contains unresolved stages. Inspect the file before resolving its conflict; no empty-index diff was assumed.')
        def blob(spec):
            resolved = git_read(self.workspace.root, ['rev-parse', '--verify', '--quiet', spec], limit=1000)
            if resolved['code'] == 1:
                return b''
            if resolved['code'] or resolved['truncated']:
                raise GitError('Cannot resolve the Git snapshot.')
            result = git_read(self.workspace.root, ['cat-file', 'blob', resolved['data'].decode().strip()])
            if result['code'] or result['truncated']:
                raise GitError('Git blob is unavailable or exceeds the 2 MB inspection limit.')
            return result['data']
        before = blob('HEAD:' + repo_path) if staged else blob(':' + repo_path)
        if staged:
            after = blob(':' + repo_path)
        index = git_read(self.workspace.root, ['ls-files', '--stage', '-z', '--', path], limit=16000)
        head = git_read(self.workspace.root, ['ls-tree', '-z', 'HEAD', '--', path], limit=16000) if metadata['head'] else {'code': 0, 'data': b'', 'truncated': False}
        if index['code'] or index['truncated'] or head['code'] or head['truncated']:
            raise GitError('Cannot inspect file modes.')
        index_mode = index['data'].split(b' ', 1)[0].decode() if index['data'] else None
        head_mode = head['data'].split(b' ', 1)[0].decode() if head['data'] else None
        current_mode = ('100755' if target.stat().st_mode & 0o111 else '100644') if target.exists() else None
        modes = {'before_mode': head_mode if staged else index_mode, 'after_mode': index_mode if staged else current_mode}
        if b'\0' in before or b'\0' in after:
            return {'path': path, 'staged': staged, 'binary': True, 'before_bytes': len(before), 'after_bytes': len(after), **modes}
        with tempfile.TemporaryDirectory(prefix='codine-git-diff-') as temporary:
            root = Path(temporary)
            (root / 'before').write_bytes(before)
            (root / 'after').write_bytes(after)
            result = git_read(root, ['diff', '--no-index', '--no-ext-diff', '--no-textconv', '--color=never', '--', 'before', 'after'], limit=60000)
        if result['code'] not in (0, 1, None):
            raise GitError('Snapshot diff failed.')
        text = result['data'].decode('utf-8', errors='replace')
        text = text.replace('diff --git a/before b/after', f'diff --git a/{path} b/{path}', 1).replace('--- a/before', '--- a/' + path, 1).replace('+++ b/after', '+++ b/' + path, 1)
        return {'path': path, 'staged': staged, 'diff': text, 'truncated': result['truncated'], **modes,
                'notice': 'Read-only snapshot comparison. Use read_file before editing; Git inspection does not refresh edit permissions.'}


def register_git_tools(registry, inspector):
    registry.register(ToolSpec('git_status', 'Inspect scoped Git changes and session baseline without modifying Git state.',
                              {'type': 'object', 'properties': {}, 'additionalProperties': False}, inspector.status))
    registry.register(ToolSpec('git_diff', 'Compare safe snapshots of one project file against the index, or staged index against HEAD. Never invokes external diff/textconv helpers.',
        {'type': 'object', 'properties': {'path': {'type': 'string'}, 'staged': {'type': 'boolean'}},
         'required': ['path'], 'additionalProperties': False}, inspector.diff))
