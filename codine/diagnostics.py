"""Create a private, content-free diagnostic JSON bundle. Never uploads anything."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import re
import stat
import uuid

from . import __version__
from .sessions import validate_messages


RECOVERY = {
    'authentication': 'Check the selected provider and key locally; do not include the key in reports.',
    'rate_limit': 'Wait for quota recovery. Inspect pending billing before requesting again.',
    'connection': 'Inspect provider availability and pending billing; do not blindly replay requests.',
    'persistence': 'Check disk space and directory access. Preserve the original session before recovery.',
    'invalid_session': 'Keep the original file. Restore a known-good backup or start a new session.',
    'unsupported_version': 'Use a compatible Codine version. Do not rewrite the session version by hand.',
    'unknown': 'Inspect local state and reproduce with offline checks before sharing diagnostic metadata.',
}


def _directory(parent, name, create=False):
    if create:
        try:
            os.mkdir(name, 0o700, dir_fd=parent)
        except FileExistsError:
            pass
    return os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)


def session_summary(root_fd, session_id):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', session_id):
        raise ValueError('Invalid session ID.')
    internal = sessions = descriptor = None
    try:
        internal = _directory(root_fd, '.codine')
        sessions = _directory(internal, 'sessions')
        descriptor = os.open(session_id + '.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=sessions)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > 20_000_000:
            raise ValueError('Invalid session file.')
        with os.fdopen(os.dup(descriptor), 'rb') as stream:
            raw = stream.read(20_000_001)
        after = os.fstat(descriptor)
        if len(raw) > 20_000_000 or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError('Session changed during diagnostic read.')
        data = json.loads(raw)
        if data.get('version') != 1:
            return {'category': 'unsupported_version'}
        messages = data['messages']
        validate_messages(messages)
        return {'category': None, 'schema_version': 1, 'message_count': len(messages),
                'tool_result_count': sum(row['role'] == 'tool' for row in messages),
                'pending_tool_batch': data.get('pending') is not None,
                'pending_charge': data.get('billing', {}).get('pending') is not None}
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return {'category': 'invalid_session'}
    finally:
        for descriptor in (descriptor, sessions, internal):
            if descriptor is not None:
                os.close(descriptor)


def create_bundle(project, session_id=None, category='unknown'):
    if category not in RECOVERY:
        raise ValueError('Unknown diagnostic category.')
    project = Path(project).resolve(strict=True)
    root_fd = os.open(project, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    internal = output = None
    try:
        summary = session_summary(root_fd, session_id) if session_id else None
        effective = (summary or {}).get('category') or category
        bundle = {'version': 1, 'created': datetime.now(timezone.utc).isoformat(),
                  'codine_version': __version__, 'python': platform.python_version(),
                  'platform': platform.system(), 'category': effective,
                  'recovery': RECOVERY[effective], 'session': summary,
                  'excluded': ['credentials', 'environment', 'paths', 'model IDs', 'prompts', 'tool arguments', 'file contents', 'exception text']}
        internal = _directory(root_fd, '.codine', create=True)
        output = _directory(internal, 'diagnostics', create=True)
        name = uuid.uuid4().hex + '.json'
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=output)
        with os.fdopen(fd, 'w') as stream:
            json.dump(bundle, stream, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        return project / '.codine' / 'diagnostics' / name
    finally:
        for descriptor in (output, internal, root_fd):
            if descriptor is not None:
                os.close(descriptor)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', default='.')
    parser.add_argument('--session')
    parser.add_argument('--category', choices=tuple(RECOVERY), default='unknown')
    args = parser.parse_args(argv)
    try:
        path = create_bundle(args.project, args.session, args.category)
    except (ValueError, OSError) as error:
        parser.error(str(error))
    print(f'Private diagnostic saved locally: {path}. Review before sharing; nothing was uploaded.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
