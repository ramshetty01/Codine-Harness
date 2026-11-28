"""Explicit offline relocation of a preserved session archive into a fresh owner."""
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from .sessions import SessionStore, now


def validate_migration(value):
    if not isinstance(value, dict):
        raise ValueError('Invalid session migration metadata.')
    if not value:
        return
    if (set(value) != {'version', 'source_id', 'source_project', 'source_sha256', 'migrated_at'}
            or value['version'] != 1 or type(value['version']) is not int
            or not isinstance(value['source_id'], str)
            or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,99}', value['source_id'])
            or not isinstance(value['source_project'], str)
            or len(value['source_project']) > 4096 or not Path(value['source_project']).is_absolute()
            or not isinstance(value['source_sha256'], str)
            or not re.fullmatch(r'[a-f0-9]{64}', value['source_sha256'])
            or not isinstance(value['migrated_at'], str) or not 0 < len(value['migrated_at']) <= 100):
        raise ValueError('Invalid session migration metadata.')


def read_regular(path, limit):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > limit:
            raise ValueError('Migration input must be a bounded single-link regular file.')
        raw = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
        if (len(raw) > limit or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
            raise ValueError('Migration input changed while reading.')
        return raw


def parse(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate migration JSON key.')
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=unique)
    except (RecursionError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError('Malformed migration input.') from error


def migrate(project, ident, from_project, *, confirmation=None, system):
    target = Path(project).expanduser().resolve()
    original = Path(from_project).expanduser()
    if not original.is_absolute():
        raise ValueError('--from-project must be an absolute original workspace path.')
    # The saved origin is an identity, not a live path to follow. A compatibility
    # symlink at the old location must not silently redefine that identity.
    original = Path(os.path.abspath(original))
    if target == original or not target.is_dir():
        raise ValueError('Choose an existing destination project different from the original path.')
    # Constructor only touches this selected project's private state; no old path
    # is created, modified or automatically searched.
    store = SessionStore(target)
    try:
        source_path = store.path(ident)
        store.acquire(ident)
        raw = read_regular(source_path, 20_000_000)
        source_hash = hashlib.sha256(raw).hexdigest()
        data = parse(raw)
        if not isinstance(data, dict):
            raise ValueError('Session archive must be an object.')
        if data.get('pending') is not None:
            raise ValueError('Pending action journal cannot migrate. Resolve it at the original project path first; no action was replayed.')
        source = store._read_session(ident, expected_project=str(original), repair_pending=False, payload=data)
        if (source.billing.get('pending') is not None or source.billing.get('blocked') or
                source.last_run.get('unknown_actions') or source.last_run.get('state') == 'running'):
            raise ValueError('Resolve uncertain actions, running state or pending/blocked billing at the original project path before migration. Original archive is unchanged.')
        inbox = source_path.with_suffix('.inbox')
        if inbox.exists() or inbox.is_symlink():
            if parse(read_regular(inbox, 600000)) != []:
                raise ValueError('Pending or malformed inbox cannot migrate. Drain or review it at the original project path; no queued task was replayed.')
        review = {'version': 1, 'source_id': ident, 'source_project': str(original),
                  'source_sha256': source_hash, 'target_project': str(target)}
        digest = hashlib.sha256(json.dumps(review, sort_keys=True).encode()).hexdigest()
        new_id = 'migrated-' + digest[:32]
        result = {**review, 'confirmation': digest, 'new_session_id': new_id,
                  'messages': len(source.messages), 'provider': source.provider, 'model': source.model,
                  'spent': source.billing.get('spent'), 'status': 'preview',
                  'notice': 'Fresh session identity; original archive preserved. No files, tools, inbox items, credentials or shared allowance are moved/reset. Old absolute paths in history remain historical. Existing saved executor choice is retained; explicit user policy/profile must be supplied again.'}
        if confirmation is None:
            return result
        if confirmation != digest:
            raise ValueError('Migration snapshot or destination changed. Preview again and confirm its exact digest.')
        destination = store.path(new_id)
        if destination.exists():
            existing = store._read_session(new_id)
            if (existing.migration.get('source_sha256') != source_hash or
                    existing.migration.get('source_id') != ident or existing.migration.get('source_project') != str(original)):
                raise ValueError('Migration destination conflicts with an unrelated session.')
            return {**result, 'status': 'already_migrated'}
        replacement = copy.deepcopy(source)
        replacement.id = new_id
        replacement.project = str(target)
        replacement.worker_tasks = {}
        replacement.branch = {}  # Ancestry belongs to the source project; migration records its origin.
        replacement.created = now()
        replacement.updated = replacement.created
        replacement.messages[0] = {'role': 'system', 'content': system}
        replacement.messages.append({'role': 'user', 'content':
            'This project was explicitly relocated. Earlier file paths, command outcomes and summaries are historical. '
            'Use current project instructions and read current files before acting. Do not replay earlier tool calls or queued work.'})
        replacement.pending = None
        replacement.git_baseline = {}
        replacement.last_run = {}
        if hasattr(replacement, 'queue_receipts'):
            replacement.queue_receipts = []
        replacement.billing.pop('shared_request', None)
        replacement.billing.pop('shared_ledger', None)
        replacement.migration = {'version': 1, 'source_id': ident, 'source_project': str(original),
                                 'source_sha256': source_hash, 'migrated_at': now()}
        # No task remains actively executing after relocation. Completed plan
        # assertions remain historical; clearing last_run removes current verification.
        for task in replacement.plan:
            if task.get('status') == 'in_progress':
                task['status'] = 'pending'
        if replacement.plan:
            replacement.plan_revision += 1
        # Hold the source lock through the save, while independently locking the
        # new deterministic ID. Existing migrated sessions are never overwritten.
        destination_store = SessionStore(target)
        try:
            destination_store.acquire(new_id)
            if destination.exists():
                raise ValueError('Migration destination appeared concurrently; inspect it before retrying.')
            destination_store.save(replacement)
        finally:
            destination_store.close()
        return {**result, 'status': 'migrated'}
    finally:
        store.close()
