"""Explicit tip forks with project-local ancestry and unchanged shared spending."""
from contextlib import contextmanager
from dataclasses import asdict
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from .budget import Budget, BudgetError, money
from .sessions import Session, validate_messages, validate_title

MAX_DEPTH = 32
MAX_TREE_NODES = 200
MAX_TREE_BYTES = 2_000_000
ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,99}\Z')


def history_hash(messages):
    return hashlib.sha256(json.dumps(messages, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def validate_branch(value, session):
    if not isinstance(value, dict):
        raise ValueError('Invalid fork provenance.')
    if not value:
        return
    if (set(value) != {'version', 'project', 'parent', 'root', 'depth', 'messages', 'history_sha256'} or
            type(value['version']) is not int or value['version'] != 1 or value['project'] != session.project or
            any(not isinstance(value[key], str) or not ID.fullmatch(value[key]) for key in ('parent', 'root')) or
            session.id in (value['parent'], value['root']) or
            type(value['depth']) is not int or not 1 <= value['depth'] <= MAX_DEPTH or
            type(value['messages']) is not int or not 1 <= value['messages'] <= len(session.messages) or
            not isinstance(value['history_sha256'], str) or
            not re.fullmatch(r'[a-f0-9]{64}', value['history_sha256'])):
        raise ValueError('Invalid fork provenance.')
    validate_messages(session.messages[:value['messages']])


@contextmanager
def settled_shared_budget(agent):
    """Inspect an existing allowance under its lock; never initialize or reset it."""
    if agent.session.provider != 'openrouter':
        yield
        return
    from .shared_budget import SharedBudget, session_owner
    path = getattr(agent, 'shared_budget_path', None)
    if path is None:
        raise BudgetError('OpenRouter forks require the shared spending ledger; local-only budgets cannot fork.')
    path = Path(path)
    lock_path = path.with_suffix('.lock')
    try:
        lock = os.open(lock_path, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        if (path.exists() or path.is_symlink() or money(agent.session.billing.get('spent', '0')) or
                agent.session.billing.get('shared_request') or agent.session.billing.get('shared_ledger')):
            raise BudgetError('Shared spending state is missing its lock; refusing to fork.')
        # Before any inference there is no allowance state to modify. The normal
        # request path will initialize the same configured shared path later.
        yield
        return
    try:
        SharedBudget._check_file(os.fstat(lock), 4_000_000)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise BudgetError('Shared spending is busy; finish the request before forking.') from None
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            if (money(agent.session.billing.get('spent', '0')) or
                    agent.session.billing.get('shared_request') or agent.session.billing.get('shared_ledger')):
                raise BudgetError('Shared spending history is missing; refusing to fork.')
            yield
            return
        with os.fdopen(fd, 'rb') as stream:
            SharedBudget._check_file(os.fstat(stream.fileno()), 4_000_000)
            raw = stream.read(4_000_001)
        if len(raw) > 4_000_000:
            raise BudgetError('Shared ledger exceeds its size limit.')
        state = json.loads(raw)
        if not isinstance(state, dict):
            raise BudgetError('Malformed shared spending state.')
        checked = object.__new__(SharedBudget)
        checked.ledger = Budget(state)
        checked.validate()
        if state.get('key_identity') != hashlib.sha256(agent.config.api_key.encode()).hexdigest():
            raise BudgetError('Shared spending key identity differs; refusing to fork.')
        if state['pending'] is not None or state['blocked'] or state.get('reservation') is not None:
            raise BudgetError('Resolve pending or blocked shared spending before forking.')
        request = agent.session.billing.get('shared_request')
        if request is not None:
            if not isinstance(request, str) or not re.fullmatch(r'[a-f0-9]{32}', request):
                raise BudgetError('Invalid shared request reference; refusing to fork.')
            if agent.session.billing.get('shared_ledger') != state['ledger_id']:
                raise BudgetError('Session references another shared spending ledger; refusing to fork.')
            checked.owner = session_owner(agent.session)
            record = checked.record(request)
            if record['phase'] != 'settled':
                raise BudgetError('Resolve unsettled session request ownership before forking.')

        yield
    finally:
        os.close(lock)


def require_idle(agent, workspace):
    session = agent.session
    if session.provider != agent.config.provider:
        raise ValueError('Session/provider mismatch; select the correct provider before forking.')
    if getattr(agent, '_in_turn', False) or session.pending or session.last_run.get('outcome') == 'running':
        raise ValueError('Finish or recover the active turn/tool journal before forking.')
    if session.billing.get('pending') is not None or session.billing.get('blocked') or (session.provider != 'openrouter' and session.billing.get('shared_request')):
        raise BudgetError('Resolve pending or blocked session spending before forking.')
    inbox = getattr(agent, 'inbox', None)
    if inbox:
        # A recovered turn's cancellation flag is not new pending work. Persisted
        # queue failures still block; explicit idle forking need not run a model first.
        if inbox.failure: inbox.check_cancel()
        if inbox.list():
            raise ValueError('Run or drop queued input before forking; queued work is never copied.')
    jobs = getattr(workspace, 'jobs', None)
    if jobs and any(row['status'] in ('starting', 'running', 'queued') or row.get('cleanup_uncertain') for row in jobs.status()):
        raise ValueError('Finish or cancel active jobs and resolve uncertain cleanup before forking.')
    validate_messages(session.messages)
    validate_branch(session.branch, session)


def fork(agent, workspace, title='', *, end=None, expected_state=None):
    validate_title(title)
    from .inbox import Inbox
    inbox = getattr(agent, 'inbox', None)
    if inbox is None or inbox.path != agent.store.path(agent.session.id).with_suffix('.inbox'):
        inbox = Inbox(agent.store, agent.session)
        agent.inbox = inbox
    with inbox.lock:
        require_idle(agent, workspace)
        with settled_shared_budget(agent):
            if expected_state is not None:
                from .conversation_rewind import state_hash
                if state_hash(agent.session, agent.redactor) != expected_state:
                    raise ValueError('Conversation state changed during preview; review again.')
            agent.save()
            parent = agent.store._read_session(agent.session.id, repair_pending=False)
            if expected_state is not None and state_hash(parent, agent.redactor) != expected_state:
                raise ValueError('Saved conversation state changed; review again.')
            if end is not None:
                from .conversation_rewind import completed_turns
                if expected_state is None or type(end) is not int or end not in [row['end'] for row in completed_turns(parent.messages)]:
                    raise ValueError('Historical fork needs a reviewed complete user-turn boundary.')
            depth = parent.branch.get('depth', 0) + 1
            if depth > MAX_DEPTH:
                raise ValueError('Fork tree depth limit reached (32).')
            child = copy.deepcopy(parent)
            fresh = Session.create(workspace.root, parent.model, parent.messages[0]['content'], provider=parent.provider)
            child.id, child.created, child.updated = fresh.id, fresh.created, fresh.updated
            child.title = title or (('Fork: ' + parent.title)[:120] if parent.title else 'Fork of ' + parent.id)
            if end is not None:
                child.messages = copy.deepcopy(parent.messages[:end])
                child.context_start = 1
                child.context_summary = {}
                child.summary_end = 1
                child.summary_user = None
                child.plan = []
                child.plan_revision = parent.plan_revision + 1
            child.branch = {'version': 1, 'project': parent.project, 'parent': parent.id,
                'root': parent.branch.get('root', parent.id), 'depth': depth,
                'messages': len(child.messages), 'history_sha256': history_hash(child.messages)}
            child.pending = None
            child.last_run = {}
            child.git_baseline = {}
            child.queue_receipts = []
            # Settled references are historical accounting, not authority to own
            # the parent's request. Shared spend stays in the same durable ledger.
            child.billing.pop('shared_request', None)
            child.billing.pop('shared_ledger', None)
            validate_branch(child.branch, child)
            if len(json.dumps(agent.redactor.data(asdict(child)), ensure_ascii=False, indent=2).encode()) > 20_000_000:
                raise ValueError('Fork snapshot exceeds the 20 MB session limit.')
            # Save through a second store so a failed child write cannot release
            # the parent lock or change the active session.
            store = type(agent.store)(workspace.root, agent.redactor)
            try:
                store.acquire(child.id)
                if store.path(child.id).exists():
                    raise ValueError('Fork identity collision; retry.')
                store.save(child)
                agent.store.close()
                agent.store.lock, agent.store.active = store.lock, store.active
                store.lock, store.active = None, None
            finally:
                store.close()
            agent.session = child
            return child


def tree(store, active_id):
    """Bounded metadata view; never repair journals, acquire sessions or run tools."""
    rows, total, scanned, truncated = {}, 0, 0, False
    with os.scandir(store.directory) as entries:
        for entry in entries:
            scanned += 1
            if scanned > 1000 or len(rows) >= MAX_TREE_NODES:
                truncated = True; break
            if not entry.name.endswith('.json') or not ID.fullmatch(entry.name[:-5]):
                continue
            try:
                info = entry.stat(follow_symlinks=False)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    continue
                if info.st_size > MAX_TREE_BYTES - total:
                    truncated = True; continue
                fd = os.open(entry.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                with os.fdopen(fd, 'rb') as stream:
                    opened = os.fstat(stream.fileno())
                    if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
                        continue
                    raw = stream.read(MAX_TREE_BYTES - total + 1)
                total += len(raw)
                if total > MAX_TREE_BYTES:
                    truncated = True; break
                session = store._read_session(entry.name[:-5], repair_pending=False, payload=json.loads(raw))
                rows[session.id] = {'id': session.id, 'parent': session.branch.get('parent'),
                    'root': session.branch.get('root', session.id), 'depth': session.branch.get('depth', 0),
                    'title': session.title, 'messages': len(session.messages), 'context_start': session.context_start,
                    'summary_end': session.summary_end, 'active': session.id == active_id}
            except (OSError, ValueError, RecursionError):
                continue
    for row in rows.values():
        row['parent_missing'] = bool(row['parent'] and row['parent'] not in rows)
    return {'sessions': sorted(rows.values(), key=lambda row:(row['root'], row['depth'], row['id'])),
            'truncated': truncated, 'notice': 'Conversation branches share project files and reviewed project memory; this is not a filesystem snapshot. Use /tree ID to resume.'}


def format_tree(result):
    lines = ['Conversation tree · * active · inherited history, independent future turns']
    for row in result['sessions']:
        parent = row['parent'] or 'root'
        if row['parent_missing']:
            parent += ' (parent unavailable or outside bounded view)'
        lines.append(f"{'*' if row['active'] else ' '} {row['id']} · {row['title'] or 'Untitled'} · parent: {parent} · depth {row['depth']} · context {row['context_start']}:{row['messages']} · summary through {row['summary_end']}")
    if result['truncated']:
        lines.append('Tree scan truncated: at most 200 sessions, 1000 directory entries and 2 MB total. /resume ID still addresses a known session.')
    lines.append(result['notice'])
    return '\n'.join(lines)
