"""Evaluation compatibility wrapper over the shared interactive/evaluation allowance."""
from .budget import BudgetError
from .shared_budget import SharedBudget


class EvaluationBudget(SharedBudget):
    def __init__(self, path, api_key):
        super().__init__(path, api_key, owner='evaluation', request_cap='0.05')
        self.request_id = None

    def reserve(self):
        self.request_id = super().reserve()
        # The evaluation adapter invokes transport immediately after reservation.
        self.dispatched(self.request_id)
        return self.request_id

    def settle(self, cost, request_id=None):
        ident = request_id or self.request_id or self.ledger.state['reservation']
        if not ident:
            raise BudgetError('No evaluation request is pending.')
        blocked = super().settle(ident, cost)
        if blocked:
            raise BudgetError(blocked)
