"""Fast in-memory UI status. No filesystem, provider, or shared-ledger reads."""
from .budget import money
from .context import estimate_tokens


def cache_context(agent, messages, schemas):
    window, output = agent.config.request_limits(agent.session.model)
    agent.context_snapshot = {'session': agent.session.id, 'model': agent.session.model,
                              'input': estimate_tokens(messages, schemas),
                              'output': output, 'limit': window}


def snapshot(agent, workspace, ui, executor=None):
    session = agent.session
    active = bool(getattr(agent, '_in_turn', False) or getattr(agent, '_request_active', False))
    inbox = getattr(agent, 'inbox', None)
    queue = {'total': 0, 'ready': 0, 'deferred': 0, 'steer': 0, 'followup': 0}
    cancel = False
    if inbox is not None and inbox.path.name == session.id + '.inbox':
        if inbox.lock.acquire(blocking=False):
            try:
                queue['total'] = len(inbox.items)
                queue['deferred'] = sum(item['id'] in inbox.deferred for item in inbox.items)
                queue['ready'] = queue['total'] - queue['deferred']
                for item in inbox.items:
                    queue[item['lane']] += 1
                agent.status_queue_snapshot = (session.id, dict(queue))
            finally:
                inbox.lock.release()
        else:
            cached = getattr(agent, 'status_queue_snapshot', None)
            queue = dict(cached[1]) if cached and cached[0] == session.id else {key: '?' for key in queue}
            queue['stale'] = True
        cancel = inbox.cancel.is_set()
    commands = getattr(ui, 'submitted_commands', None)
    queue['commands'] = commands.qsize() if commands is not None else 0
    if active and cancel:
        phase = 'STOP REQUESTED'
    elif getattr(ui, '_approval_pending', False):
        phase = 'REVIEW APPROVAL'
    elif active:
        phase = {'preflight': 'preparing', 'accounting': 'accounting', 'provider': 'provider',
                 'processing': 'processing', 'tool': 'tool running'}.get(getattr(agent, '_phase', ''), 'working')
    elif getattr(ui, 'busy_reader', None):
        phase = 'finishing input'
    else:
        phase = 'idle' + (' (cancelled)' if session.last_run.get('state') == 'cancelled' else '')
    context = getattr(agent, 'context_snapshot', None)
    if not isinstance(context, dict) or context.get('session') != session.id or context.get('model') != session.model:
        context = None
    else:
        context = dict(context)
    billing = dict(session.billing)
    def amount(key):
        value = billing.get(key)
        if value is None or (isinstance(value, str) and len(value) > 128):
            return None
        try:
            return str(money(value))
        except ValueError:
            return None
    return {'mode': workspace.mode, 'executor': executor or getattr(workspace, 'executor', 'host'),
            'model': session.model, 'phase': phase, 'queue': queue, 'context': context,
            'cost': {'mode': 'unmetered' if session.provider == 'custom' else 'accounted', 'spent': amount('spent'), 'limit': amount('limit'),
                     'pending': amount('pending'), 'unsettled': billing.get('pending') is not None,
                     'blocked': bool(billing.get('blocked'))}}


def lines(state, *, compact=False):
    mode = {'ask': 'ask before edits', 'plan': 'PLAN · read only', 'auto-edit': 'auto-edit'}.get(state.get('mode'), 'ask')
    execution = 'Docker' if state.get('executor') == 'docker' else 'host'
    result = [f'{mode} · {execution}' + (f' · {state["model"]}' if state.get('model') else '')]
    queue = state.get('queue', {})
    phase = state.get('phase', 'idle')
    if compact:
        result.append(f'{phase} · Q{queue.get("total",0)} D{queue.get("deferred",0)} C{queue.get("commands",0)}' + (' ~' if queue.get('stale') else ''))
    else:
        result.append(f'{phase} · queued {queue.get("total",0)}: {queue.get("ready",0)} ready, '
                      f'{queue.get("deferred",0)} deferred; {queue.get("commands",0)} commands for prompt')
        if queue.get('stale'):
            result.append('Queue counts are the last available snapshot; queue update is underway.')
        result.append(f'Queue lanes: {queue.get("steer",0)} steering, {queue.get("followup",0)} follow-ups. '
                      'Deferred input needs /next; /queue reviews it.')
    context = state.get('context')
    if context:
        result.append(f'ctx est {context["input"]}+{context["output"]}/{context["limit"]} · last prepared')
        if not compact:
            result.append('Context is a cached byte-based estimate of last prepared input + output reservation / local limit, '
                          'not provider tokenization, cumulative usage, or next-request occupancy. New input/results are not included.')
    else:
        result.append('ctx est unknown · no prepared snapshot')
    cost = state.get('cost', {})
    if cost.get('mode')=='unmetered':
        result.append('cost unknown · custom unmetered · no USD cap')
        return result
    spent = '$' + cost['spent'] if cost.get('spent') is not None else 'unknown'
    if cost.get('unsettled'):
        amount = '$' + cost['pending'] if cost.get('pending') is not None else 'unknown'
        result.append(f'cost {spent} · reserved {amount}, unsettled')
    else:
        result.append(f'cost {spent}' + (' · BLOCKED' if cost.get('blocked') else ' · no pending reservation'))
    if not compact:
        result.append('Cost is local session accounting only; an unsettled reservation is not a measured charge. '
                      'Interrupted/missing-cost requests remain unknown. /budget checks the shared allowance.')
    return result
