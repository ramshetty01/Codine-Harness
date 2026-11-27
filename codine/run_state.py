"""Run outcomes describe harness state, never certify model claims as verified work."""
import uuid
from .sessions import now

OUTCOMES = {
    'running': ('running', 'started', False),
    'completed': ('awaiting_input', 'model_stop', False),
    'output_limit': ('paused', 'output_limit', True),
    'step_limit': ('paused', 'step_limit', True),
    'loop_limit': ('paused', 'loop_limit', False),
    'restart': ('restarting', 'restart_requested', True),
    'interrupted': ('cancelled', 'user_cancelled', False),
    'failed': ('failed', 'execution_error', False),
    'command': ('awaiting_input', 'command', False),
}
ERROR_STATES = {'budget_blocked': 'blocked', 'context_limit': 'blocked',
                'provider_error': 'failed', 'persistence_error': 'failed',
                'execution_error': 'failed', 'configuration_error': 'blocked'}


def task_state(session, started_revision):
    statuses = [item['status'] for item in session.plan]
    if not statuses:
        return 'no_plan'
    if 'blocked' in statuses:
        return 'blocked'
    if any(status != 'completed' for status in statuses):
        return 'unresolved'
    # An old completed plan is not evidence about a new user request.
    return 'reported_complete' if session.plan_revision != started_revision else 'stale_plan'


def run_record(session, outcome, started_revision, *, previous=None, reason=None, unknown_actions=False):
    state, default_reason, retryable = OUTCOMES[outcome]
    reason = reason or default_reason
    state = ERROR_STATES.get(reason, state)
    task = task_state(session, started_revision)
    pending_charge = session.billing.get('pending') is not None
    if unknown_actions or pending_charge or task == 'blocked':
        retryable = False
    previous = previous or {}
    return {'version': 1, 'id': previous.get('id', uuid.uuid4().hex),
            'started_at': previous.get('started_at', now()),
            'finished_at': None if outcome == 'running' else now(),
            'outcome': outcome, 'state': state, 'reason': reason,
            'retryable': retryable, 'verification': 'unverified',
            'task_state': task, 'started_plan_revision': started_revision,
            'plan_revision': session.plan_revision, 'unknown_actions': unknown_actions,
            'pending_charge': pending_charge, 'persistence': 'saved'}


def validate_run(record):
    if not isinstance(record, dict):
        raise ValueError('Invalid saved run state.')
    if not record:
        return
    expected = {'version', 'id', 'started_at', 'finished_at', 'outcome', 'state', 'reason',
                'retryable', 'verification', 'task_state', 'started_plan_revision',
                'plan_revision', 'unknown_actions', 'pending_charge', 'persistence'}
    if set(record) != expected or type(record['version']) is not int or record['version'] != 1:
        raise ValueError('Unsupported saved run state.')
    if (record['outcome'] not in OUTCOMES or record['state'] not in
            {value[0] for value in OUTCOMES.values()} | set(ERROR_STATES.values()) or
            record['reason'] not in {value[1] for value in OUTCOMES.values()} | set(ERROR_STATES)):
        raise ValueError('Invalid run outcome/reason.')
    default_state, default_reason, _ = OUTCOMES[record['outcome']]
    allowed_reasons = {default_reason} | (set(ERROR_STATES) if record['outcome'] == 'failed' else set())
    if record['reason'] not in allowed_reasons or record['state'] != ERROR_STATES.get(record['reason'], default_state):
        raise ValueError('Inconsistent run state/reason.')
    if record['retryable'] and (record['unknown_actions'] or record['pending_charge']):
        raise ValueError('Uncertain actions or charges cannot be marked retryable.')
    if (record['verification'] != 'unverified' or
            record['task_state'] not in {'no_plan', 'blocked', 'unresolved', 'reported_complete', 'stale_plan'} or
            record['persistence'] not in {'saved', 'failed', 'not_attempted'}):
        raise ValueError('Invalid run verification/task state.')
    if any(type(record[key]) is not bool for key in ('retryable', 'unknown_actions', 'pending_charge')):
        raise ValueError('Invalid run flags.')
    if any(type(record[key]) is not int or record[key] < 0 for key in ('plan_revision', 'started_plan_revision')):
        raise ValueError('Invalid run plan revision.')
    if any(not isinstance(record[key], str) or not record[key] or len(record[key]) > 100 for key in ('id', 'started_at')):
        raise ValueError('Invalid run identity.')
    if record['finished_at'] is not None and (not isinstance(record['finished_at'], str) or len(record['finished_at']) > 100):
        raise ValueError('Invalid run end time.')
