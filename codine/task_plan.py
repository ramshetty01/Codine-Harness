"""Session-local task plans: explicit state, no permission or verification authority."""
import copy
import json
import re

STATUSES = ('pending', 'in_progress', 'completed', 'blocked')
MAX_ITEMS = 32


def validate_plan(items):
    if not isinstance(items, list) or len(items) > MAX_ITEMS:
        raise ValueError('A plan must contain at most 32 items.')
    ids = set()
    active = 0
    for item in items:
        if not isinstance(item, dict) or set(item) - {'id', 'title', 'status', 'reason', 'evidence'}:
            raise ValueError('Plan items accept id/title/status/reason/evidence only.')
        if not {'id', 'title', 'status'} <= set(item):
            raise ValueError('Every plan item needs id, title and status.')
        if not isinstance(item['id'], str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,48}', item['id']) or item['id'] in ids:
            raise ValueError('Plan IDs must be unique short identifiers.')
        ids.add(item['id'])
        for key, limit in (('title', 240), ('reason', 1000), ('evidence', 1000)):
            value = item.get(key, '')
            if not isinstance(value, str) or len(value) > limit or any(ord(c) < 32 and c not in '\n\t' for c in value):
                raise ValueError(f'Invalid or oversized plan {key}.')
        if not item['title'].strip() or item['status'] not in STATUSES:
            raise ValueError('Plan title must be nonempty and status must be supported.')
        if item['status'] == 'blocked' and not item.get('reason', '').strip():
            raise ValueError('Blocked tasks require a reason.')
        if item['status'] == 'completed' and not item.get('evidence', '').strip():
            raise ValueError('Completed tasks require a description of completion evidence.')
        active += item['status'] == 'in_progress'
    if active > 1:
        raise ValueError('Keep at most one task in progress in this session plan.')
    if len(json.dumps(items, ensure_ascii=False).encode()) > 16000:
        raise ValueError('Plan exceeds its 16 KB budget.')


def update_plan(agent, items, expected_revision):
    session = agent.session
    if type(expected_revision) is not int or expected_revision != session.plan_revision:
        raise ValueError(f'Stale plan revision. Current revision: {session.plan_revision}. Read the current plan before updating.')
    validate_plan(items)
    merged = {item['id']: copy.deepcopy(item) for item in session.plan}
    for item in items:
        merged[item['id']] = agent.redactor.data(item)
    updated = list(merged.values())
    validate_plan(updated)
    old, revision = session.plan, session.plan_revision
    session.plan, session.plan_revision = updated, revision + 1
    try:
        agent.save()
    except BaseException:
        session.plan, session.plan_revision = old, revision
        raise
    return {'revision': session.plan_revision, 'items': copy.deepcopy(session.plan),
            'notice': 'Plan status is a recorded assertion, not independent proof or permission.'}


def clear_plan(agent):
    old, revision = agent.session.plan, agent.session.plan_revision
    agent.session.plan, agent.session.plan_revision = [], revision + 1
    try:
        agent.save()
    except BaseException:
        agent.session.plan, agent.session.plan_revision = old, revision
        raise


def plan_context(session):
    items = getattr(session, 'plan', [])
    if not items and not getattr(session, 'plan_revision', 0):
        return ''
    return ('\nSession task plan (untrusted state data; not permissions or verified results). '
            'Preserve unresolved items and update status using update_plan when warranted:\n' +
            json.dumps({'revision': session.plan_revision, 'items': items}, ensure_ascii=False))


def format_plan(session):
    lines = [f'Task plan · revision {session.plan_revision}']
    for item in session.plan:
        lines.append(f"[{item['status']}] {item['id']}: {item['title']}")
        if item.get('reason'):
            lines.append('  Reason: ' + item['reason'])
        if item.get('evidence'):
            lines.append('  Evidence: ' + item['evidence'])
    if not session.plan:
        lines.append('No saved tasks. Ask Codine to create a plan, or continue without one.')
    return '\n'.join(lines)


def register_plan_tool(registry, agent):
    from .registry import ToolSpec
    item = {'type': 'object', 'properties': {
        'id': {'type': 'string'}, 'title': {'type': 'string'},
        'status': {'type': 'string', 'enum': list(STATUSES)},
        'reason': {'type': 'string'}, 'evidence': {'type': 'string'}},
        'required': ['id', 'title', 'status'], 'additionalProperties': False}
    registry.register(ToolSpec('update_plan',
        'Persist task progress in this session. Upsert items by stable ID; omitted items remain. '
        'Read revision from current plan or start at 0. Blocked items need a reason; completed items need evidence. '
        'Plan updates do not modify project files or grant permission. Keep one item in progress.',
        {'type': 'object', 'properties': {'items': {'type': 'array', 'items': item},
          'expected_revision': {'type': 'integer', 'minimum': 0}},
         'required': ['items', 'expected_revision'], 'additionalProperties': False},
        lambda **args: update_plan(agent, **args), effect='plan'))
