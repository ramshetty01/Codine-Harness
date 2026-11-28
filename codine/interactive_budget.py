"""CLI-only shared accounting; scripted/evaluation agents inject their own guard."""
import uuid
from .budget import Budget, BudgetError, money
from .shared_budget import SharedBudget, session_owner


def open_shared(agent):
    try:
        return SharedBudget(agent.shared_budget_path, agent.config.api_key,
                            owner=session_owner(agent.session), request_cap=agent.config.request_budget_usd)
    except BlockingIOError:
        raise BudgetError('Shared allowance is busy in another process. No request was sent.') from None


def synchronize(agent, local, shared):
    ident = local.state.get('shared_request')
    if ident is None:
        return
    if local.state.get('shared_ledger') != shared.ledger.state['ledger_id']:
        raise BudgetError('Session references another allowance domain/ledger. Review it before continuing.')
    if ident not in shared.ledger.state['records']:
        # Local intent was saved but its shared reservation never committed.
        # Receipts are never pruned, and the durable ledger UUID must match.
        if local.state['pending'] is not None:
            local.settle('0')
        local.state.pop('shared_request', None)
        agent.save()
        return
    record = shared.record(ident)
    if local.state['pending'] is not None:
        if record['phase'] == 'reserved':
            # A prior process died before its durable dispatch marker. The lock
            # proves it is no longer running; dispatch was never authorized.
            shared.settle(ident, '0')
            record = shared.record(ident)
        if record['phase'] == 'settled':
            blocked = local.settle(record['cost'])
            agent.save()
            if blocked:
                raise BudgetError(blocked)


def complete_shared(agent, messages, schemas, local, emit, *, completion=None):
    with open_shared(agent) as shared:
        synchronize(agent, local, shared)
        local.reserve()
        ident = uuid.uuid4().hex
        local.state['shared_request'] = ident
        local.state['shared_ledger'] = shared.ledger.state['ledger_id']
        agent.save()
        reserved = False
        try:
            shared.reserve(ident)
            reserved = True
            details = agent.budget_metadata('key', agent.config.api_key)
            remaining = details.get('limit_remaining') if isinstance(details, dict) else None
            threshold = max(money('1.55'), money('1.50') + money(agent.config.request_budget_usd))
            if remaining is None or money(remaining) < threshold:
                raise BudgetError(f'Key must have at least ${threshold} remaining to preserve the $1.50 reserve. No inference sent.')
            boundary = getattr(agent, 'queued_boundary', None)
            if callable(boundary):
                boundary()  # A stop during metadata lookup is still proven non-dispatch.
            shared.dispatched(ident)
        except BaseException:
            # This block precedes the only provider call. Zero here is proven
            # local non-dispatch, never an inference from an HTTP/model error.
            own_record = shared.ledger.state['records'].get(ident)
            if own_record is not None and own_record['owner'] == shared.owner:
                shared.settle(ident, '0')
            local.settle('0')
            if not reserved:
                local.state.pop('shared_request', None)
            agent.save()
            raise
        agent._phase = 'provider'
        reply, finish, usage = (completion(emit) if completion is not None else
                                agent.provider.complete(messages, schemas, agent.session.model, emit))
        agent._phase = 'processing'
        cost = getattr(usage, 'cost', None) if usage else None
        if cost is None:
            raise BudgetError('Provider omitted cost. Shared request remains pending; inspect /budget for its owner/request ID.')
        shared_blocked = shared.settle(ident, cost)
        local_blocked = local.settle(cost)
        agent.save()
        agent.emit('status', shared.summary() + '\n' + local.summary())
        if shared_blocked or local_blocked:
            raise BudgetError(shared_blocked or local_blocked)
        return reply, finish, usage


def reconcile_shared(agent, request_id, cost):
    if not agent.session.billing or agent.session.billing.get('shared_request') != request_id:
        raise BudgetError('Reconciliation must name this session’s exact pending request ID.')
    local = Budget(agent.session.billing)
    with open_shared(agent) as shared:
        # record() checks the durable owner; an imported session has a new owner.
        if local.state.get('shared_ledger') != shared.ledger.state['ledger_id']:
            raise BudgetError('Session refers to a different allowance ledger.')
        record = shared.record(request_id)
        blocked = shared.settle(request_id, cost)
        if local.state['pending'] is not None:
            local_blocked = local.settle(record['cost'])
            blocked = blocked or local_blocked
        agent.save()
        return shared.summary() + '\n' + local.summary() + ('\n' + blocked if blocked else '')


def budget_status(agent):
    with open_shared(agent) as shared:
        if agent.session.billing:
            synchronize(agent, Budget(agent.session.billing), shared)
        return shared.summary()
