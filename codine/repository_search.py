"""Bounded repository discovery, isolated Git ignore matching, and timed regex work."""
from functools import lru_cache
import fnmatch
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import sys
import tempfile
import time

MAX_ENTRIES = 20000
MAX_CANDIDATES = 4000
MAX_BYTES = 8_000_000
SCAN_SECONDS = 2.0
REGEX_SECONDS = 1.0
MAX_IGNORES = 64
MAX_IGNORE_BYTES = 131072

REGEX_WORKER = r'''
import json, re, sys
payload = json.load(sys.stdin)
try:
    pattern = re.compile(payload['query'], 0 if payload['case_sensitive'] else re.IGNORECASE)
    matches = []
    truncated = False
    for path, content in payload['files']:
        for number, line in enumerate(content.splitlines(), 1):
            match = pattern.search(line)
            if match:
                if len(matches) == payload['limit']:
                    truncated = True
                    break
                start = max(0, match.start() - 80)
                matches.append({'path': path, 'line': number, 'column': match.start() + 1,
                                'text_start_column': start + 1, 'text': line[start:start + 1000]})
        if truncated: break
    print(json.dumps({'matches': matches, 'truncated': truncated}))
except (re.error, RecursionError) as error:
    print(json.dumps({'error': str(error)[:200]}))
'''


def _pattern(pattern):
    if not isinstance(pattern, str) or not pattern or len(pattern) > 256 or '\0' in pattern:
        raise ValueError('Glob must be a nonempty pattern of at most 256 characters.')
    if pattern.startswith('/') or '..' in pattern.split('/') or '\\' in pattern:
        raise ValueError('Use a project-relative POSIX glob without traversal.')


def glob_match(name, pattern):
    if '/' not in pattern:
        return fnmatch.fnmatchcase(PurePosixPath(name).name, pattern)
    parts, patterns = tuple(name.split('/')), tuple(pattern.split('/'))
    @lru_cache(maxsize=None)
    def match(i, j):
        if j == len(patterns): return i == len(parts)
        if patterns[j] == '**':
            return match(i, j + 1) or (i < len(parts) and match(i + 1, j))
        return i < len(parts) and fnmatch.fnmatchcase(parts[i], patterns[j]) and match(i + 1, j + 1)
    return match(0, 0)


def ignored_paths(paths, ignores, project_root):
    """Match copied ignore text, never the project's Git config, index or hooks."""
    if not ignores or not paths:
        return set()
    git = shutil.which('git')
    if not git:
        raise ValueError('Git is required to honor .gitignore files; install Git or explicitly disable respect_ignore.')
    if Path(git).resolve().is_relative_to(project_root):
        raise ValueError('The Git executable must be outside the model-editable project.')
    with tempfile.TemporaryDirectory(prefix='codine-ignore-') as directory:
        root = Path(directory)
        metadata = root / '.git'
        (metadata / 'objects').mkdir(parents=True)
        (metadata / 'refs').mkdir()
        (metadata / 'HEAD').write_text('ref: refs/heads/main\n')
        for name in paths:
            (root / name).parent.mkdir(parents=True, exist_ok=True)
        for name, text in ignores.items():
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding='utf-8')
        env = {'PATH': '/usr/bin:/bin', 'HOME': directory, 'GIT_CONFIG_NOSYSTEM': '1',
               'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_SYSTEM': os.devnull,
               'GIT_TERMINAL_PROMPT': '0', 'LC_ALL': 'C'}
        command = [git, '--no-optional-locks', '--git-dir=' + str(metadata), '--work-tree=' + directory,
                   '-c', 'core.fsmonitor=false', '-c', 'core.untrackedCache=false',
                   '-c', 'core.excludesFile=' + os.devnull, 'check-ignore', '--no-index', '--stdin', '-z']
        try:
            result = subprocess.run(command, input=('\0'.join(paths) + '\0').encode(),
                capture_output=True, cwd=root, env=env, timeout=1.0)
        except subprocess.TimeoutExpired as error:
            raise ValueError('Ignore matching exceeded its deadline; narrow the search.') from error
        if result.returncode not in (0, 1):
            raise ValueError('Ignore matching failed; no unfiltered results returned.')
        return set(result.stdout.decode('utf-8').rstrip('\0').split('\0')) if result.stdout else set()


def discover(workspace, path='.', recursive=True, glob='*', respect_ignore=True):
    _pattern(glob)
    if type(recursive) is not bool or type(respect_ignore) is not bool:
        raise ValueError('recursive and respect_ignore must be booleans.')
    target = workspace.resolve(path, directory=True)
    if not target.exists():
        raise ValueError('Search path does not exist.')
    names, ignores = [], {}
    scanned, ignore_bytes = 0, 0
    reason = None
    deadline = time.monotonic() + SCAN_SECONDS

    def read_ignore(relative):
        nonlocal ignore_bytes
        if not respect_ignore or relative in ignores: return
        candidate = workspace.root / relative
        if not candidate.exists() and not candidate.is_symlink(): return
        data, text = workspace._read(workspace.resolve(relative))
        if len(ignores) >= MAX_IGNORES or len(data) > 16384 or ignore_bytes + len(data) > MAX_IGNORE_BYTES:
            raise ValueError('Ignore files exceed the bounded ignore allowance; narrow scope or explicitly disable respect_ignore.')
        ignore_bytes += len(data)
        ignores[relative] = text

    # Include ancestor ignore rules when searching a nested directory or file.
    relative_target = target.relative_to(workspace.root)
    ancestors = relative_target.parts if target.is_dir() else relative_target.parts[:-1]
    read_ignore('.gitignore')
    prefix = Path()
    for part in ancestors:
        prefix /= part
        read_ignore((prefix / '.gitignore').as_posix())

    def walk(descriptor, relative):
        nonlocal scanned, reason
        entries = []
        with os.scandir(descriptor) as iterator:
            for entry in iterator:
                scanned += 1
                if scanned > MAX_ENTRIES:
                    reason = 'scan_limit'; break
                if time.monotonic() > deadline:
                    reason = 'deadline'; break
                entries.append(entry)
        for entry in sorted(entries, key=lambda item: item.name):
            if time.monotonic() > deadline:
                reason = 'deadline'; return
            name = (relative / entry.name).as_posix()
            if len(name.encode('utf-8')) > 1024: continue
            try:
                mode = entry.stat(follow_symlinks=False)
                workspace.resolve(name, directory=stat.S_ISDIR(mode.st_mode))
                if stat.S_ISDIR(mode.st_mode):
                    if recursive and reason is None:
                        child = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                        try:
                            read_ignore((Path(name) / '.gitignore').as_posix())
                            walk(child, Path(name))
                        finally:
                            os.close(child)
                elif stat.S_ISREG(mode.st_mode) and mode.st_nlink == 1:
                    if entry.name == '.gitignore': read_ignore(name)
                    if glob_match(name, glob):
                        names.append(name)
                        if len(names) >= MAX_CANDIDATES:
                            reason = 'file_limit'; return
            except (OSError, UnicodeError):
                continue
            except ValueError:
                # Policy/exclusion denials never disclose paths in results.
                if entry.name == '.gitignore' and respect_ignore: raise
                continue
            if reason == 'file_limit': return

    if target.is_file():
        mode = target.stat(follow_symlinks=False)
        if stat.S_ISREG(mode.st_mode) and mode.st_nlink == 1 and glob_match(relative_target.as_posix(), glob):
            names.append(relative_target.as_posix())
    elif target.is_dir():
        # Walk descriptors rather than following a swapped directory symlink.
        root_fd = os.open(workspace.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptor = root_fd
        try:
            for part in relative_target.parts:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                if descriptor != root_fd: os.close(descriptor)
                descriptor = child
            walk(descriptor, relative_target)
        finally:
            if descriptor != root_fd: os.close(descriptor)
            os.close(root_fd)
    else:
        raise ValueError('Only regular project files and directories can be searched.')
    excluded = ignored_paths(names, ignores, workspace.root) if respect_ignore else set()
    return {'files': sorted(name for name in names if name not in excluded),
            'truncated': reason is not None, 'reason': reason, 'scanned_entries': scanned}


def list_files(workspace, path='.', recursive=True, limit=200, glob='*', respect_ignore=True):
    if type(limit) is not int or not 1 <= limit <= 2000:
        raise ValueError('File limit must be between 1 and 2000.')
    result = discover(workspace, path, recursive, glob, respect_ignore)
    if len(result['files']) > limit:
        result.update(files=result['files'][:limit], truncated=True, reason='result_limit')
    if result['truncated']: result['hint'] = 'Narrow path or glob; this is not a complete repository inventory.'
    return result


def search(workspace, query, path='.', limit=50, glob='*', regex=False, case_sensitive=True, respect_ignore=True):
    if type(regex) is not bool or type(case_sensitive) is not bool:
        raise ValueError('regex and case_sensitive must be booleans.')
    if not isinstance(query, str) or not query or len(query) > 1024 or '\0' in query:
        raise ValueError('Query must contain 1–1024 characters without NUL.')
    if type(limit) is not int or not 1 <= limit <= 200:
        raise ValueError('Match limit must be between 1 and 200.')
    listing = discover(workspace, path, True, glob, respect_ignore)
    files, total = [], 0
    for name in listing['files']:
        try:
            data, content = workspace._read(workspace.resolve(name))
        except (ValueError, UnicodeError, OSError):
            continue
        if total + len(data) > MAX_BYTES:
            listing.update(truncated=True, reason='byte_limit'); break
        total += len(data)
        files.append((name, content))
    if regex:
        try:
            process = subprocess.run([sys.executable, '-I', '-c', REGEX_WORKER],
                input=json.dumps({'query': query, 'files': files, 'limit': limit, 'case_sensitive': case_sensitive}),
                capture_output=True, text=True, timeout=REGEX_SECONDS,
                env={key: os.environ[key] for key in ('PATH', 'LANG') if key in os.environ})
        except subprocess.TimeoutExpired:
            return {'matches': [], 'truncated': True, 'reason': 'regex_timeout',
                    'hint': 'Regex exceeded its isolated deadline. Simplify it or narrow scope.'}
        if process.returncode != 0: raise ValueError('Regex worker failed; no partial results returned.')
        result = json.loads(process.stdout)
        if 'error' in result: raise ValueError('Invalid regex: ' + result['error'])
    else:
        matches = []
        needle = query if case_sensitive else query.casefold()
        for name, content in files:
            for number, line in enumerate(content.splitlines(), 1):
                position = (line if case_sensitive else line.casefold()).find(needle)
                if position >= 0:
                    if not case_sensitive:
                        folded_offset = 0
                        for original_position, char in enumerate(line):
                            folded_offset += len(char.casefold())
                            if folded_offset > position:
                                position = original_position; break
                    start = max(0, position - 80)
                    matches.append({'path': name, 'line': number, 'column': position + 1,
                                    'text_start_column': start + 1, 'text': line[start:start + 1000]})
                    if len(matches) > limit: break
            if len(matches) > limit: break
        result = {'matches': matches[:limit], 'truncated': len(matches) > limit}
    reason = 'result_limit' if result['truncated'] else listing['reason']
    result.update(truncated=result['truncated'] or listing['truncated'], reason=reason,
                  scanned_files=len(files), scanned_bytes=total)
    if result['truncated']: result['hint'] = 'Narrow path/glob/query; no claim of complete coverage.'
    return result
