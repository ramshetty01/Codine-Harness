"""Conservative session ledger. Remote key limits remain the billing boundary."""
from decimal import Decimal, InvalidOperation


class BudgetError(ValueError):
    pass


def money(value):
    try:
        if isinstance(value, bool):
            raise InvalidOperation()
        result = Decimal(str(value))
        if not result.is_finite() or result < 0:
            raise InvalidOperation()
        return result
    except (InvalidOperation, ValueError):
        raise BudgetError('Cost must be a finite nonnegative USD amount.') from None


class Budget:
    def __init__(self, state):
        if not isinstance(state, dict) or not {'limit', 'request_cap', 'spent', 'pending', 'blocked'} <= state.keys():
            raise BudgetError('Malformed session budget ledger.')
        self.state = state
        for key in ('limit', 'request_cap', 'spent'):
            money(state[key])
        if not 0 < money(state['request_cap']) <= money(state['limit']):
            raise BudgetError('Invalid request reservation or limit.')
        if not isinstance(state['blocked'], str):
            raise BudgetError('Invalid budget stop reason.')
        if state.get('pending') is not None and money(state['pending']) != money(state['request_cap']):
            raise BudgetError('Pending reservation differs from the configured request cap.')

    @classmethod
    def create(cls, limit='3.50', request_cap='0.25'):
        limit, cap = money(limit), money(request_cap)
        if not 0 < cap <= limit:
            raise BudgetError('Request reservation must be positive and no larger than the session budget.')
        return cls({'limit': str(limit), 'request_cap': str(cap), 'spent': '0', 'pending': None, 'blocked': ''})

    def reserve(self):
        if self.state.get('blocked'):
            raise BudgetError(self.state['blocked'])
        if self.state.get('pending') is not None:
            raise BudgetError('Previous request cost is unknown. Check OpenRouter activity, then use /budget reconcile AMOUNT. No retry was sent.')
        cap = money(self.state['request_cap'])
        if money(self.state['spent']) + cap > money(self.state['limit']):
            raise BudgetError('Session budget cannot cover another request reservation. No request was sent.')
        self.state['pending'] = str(cap)

    def settle(self, cost):
        if self.state.get('pending') is None:
            raise BudgetError('No pending request to reconcile.')
        actual = money(cost)
        reserved = money(self.state['pending'])
        self.state['spent'] = str(money(self.state['spent']) + actual)
        self.state['pending'] = None
        if actual > reserved or money(self.state['spent']) > money(self.state['limit']):
            self.state['blocked'] = 'Actual charge exceeded the local reservation/budget. Further requests blocked; inspect provider pricing and key limits.'
        return self.state.get('blocked', '')

    def summary(self):
        return (f"Session USD: ${self.state['spent']} charged / ${self.state['limit']} limit; "
                f"${self.state['request_cap']} reserved per request; pending={self.state.get('pending')}. "
                + self.state.get('blocked', ''))
