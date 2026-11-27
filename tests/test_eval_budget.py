import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
from codine.budget import Budget, BudgetError
from codine.eval_budget import EvaluationBudget
from codine.live_evals import GuardedProvider


class EvalBudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'budget.json'

    def test_pending_survives_restart_and_key_changes_fail(self):
        budget = EvaluationBudget(self.path, 'test-key')
        budget.reserve()
        budget.close()
        reopened = EvaluationBudget(self.path, 'test-key')
        with self.assertRaises(BudgetError): reopened.reserve()
        reopened.settle('0.003')
        reopened.close()
        with self.assertRaises(BudgetError): EvaluationBudget(self.path, 'different-key')
        reopened = EvaluationBudget(self.path, 'test-key')
        self.assertEqual(reopened.ledger.state['spent'], '0.003')
        reopened.close()
        self.assertNotIn('test-key', self.path.read_text())

    def test_concurrent_run_is_rejected(self):
        budget = EvaluationBudget(self.path, 'key')
        try:
            with self.assertRaises(BlockingIOError): EvaluationBudget(self.path, 'key')
        finally: budget.close()

    def test_guard_blocks_small_key_and_oversized_inputs_before_transport(self):
        budget = EvaluationBudget(self.path, 'key')
        self.addCleanup(budget.close)
        transport = Mock()
        provider = GuardedProvider(transport, budget, Budget.create('.25','.05'), 'key', lambda *a: {'limit_remaining': '1.54'})
        with self.assertRaises(BudgetError): provider.complete([], [], 'model', lambda *a: None)
        with self.assertRaises(BudgetError): provider.complete(['x'*31000], [], 'model', lambda *a: None)
        transport.complete.assert_not_called()
        self.assertIsNone(budget.ledger.state['pending'])

    def test_unknown_cost_blocks_followup_and_no_retry(self):
        budget = EvaluationBudget(self.path, 'key')
        self.addCleanup(budget.close)
        transport = Mock()
        transport.complete.return_value = ({}, 'stop', SimpleNamespace(cost=None))
        provider = GuardedProvider(transport, budget, Budget.create('.25','.05'), 'key', lambda *a: {'limit_remaining': 5})
        with self.assertRaises(BudgetError): provider.complete([], [], 'model', lambda *a: None)
        with self.assertRaises(BudgetError): provider.complete([], [], 'model', lambda *a: None)
        self.assertEqual(transport.complete.call_count, 1)
        self.assertEqual(json.loads(self.path.read_text())['pending'], '0.05')

    def test_actual_cost_settles_shared_and_run_ledgers(self):
        budget = EvaluationBudget(self.path, 'key')
        self.addCleanup(budget.close)
        transport = Mock()
        transport.complete.return_value = ({}, 'stop', SimpleNamespace(cost=.001))
        local = Budget.create('.05','.05')
        provider = GuardedProvider(transport, budget, local, 'key', lambda *a: {'limit_remaining': 5})
        provider.complete([], [], 'model', lambda *a: None)
        self.assertEqual(local.state['spent'], '0.001')
        self.assertEqual(budget.ledger.state['spent'], '0.001')
        with self.assertRaises(BudgetError): provider.complete([], [], 'model', lambda *a: None)
        self.assertEqual(transport.complete.call_count, 1)
