import hashlib
import json
import multiprocessing
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from codine.agent import Agent, SYSTEM, PersistenceError
from codine.budget import Budget, BudgetError
from codine.config import Config, Redactor
from codine.eval_budget import EvaluationBudget
from codine.interactive_budget import budget_status, reconcile_shared
from codine.sessions import Session, SessionStore
from codine.shared_budget import SharedBudget, session_owner


def reserve_worker(path, started, release, queue):
    try:
        with SharedBudget(path, 'fake-key', owner='worker') as budget:
            ident = budget.reserve(); budget.dispatched(ident)
            started.set(); release.wait(10)
        queue.put('reserved')
    except BaseException as error:
        queue.put(type(error).__name__)


class SharedBudgetTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.path = self.root / 'shared' / 'budget.json'

    def agent(self, name='project', cost='0.01', metadata=None):
        root = self.root / name; root.mkdir(exist_ok=True)
        store = SessionStore(root); self.addCleanup(store.close)
        session = Session.create(root, 'offline-model', SYSTEM, provider='openrouter')
        local = Budget.create(); session.billing = local.state
        provider = Mock()
        provider.complete.return_value = ({'role': 'assistant', 'content': 'ok'}, 'stop',
            SimpleNamespace(cost=cost, prompt_tokens=1, completion_tokens=1, total_tokens=2))
        agent = Agent(Config(root, provider='openrouter', api_key='fake-key'), provider,
                      None, store, session, Mock(), Redactor())
        agent.configure_shared_budget(self.path, metadata or Mock(return_value={'limit_remaining': 5}))
        return agent, local

    def test_legacy_evaluation_total_and_pending_are_preserved(self):
        self.path.parent.mkdir()
        state = Budget.create('3.50', '0.05').state
        state.update(spent='0.011877392', pending='0.05', key_identity=hashlib.sha256(b'fake-key').hexdigest())
        self.path.write_text(json.dumps(state))
        with SharedBudget(self.path, 'fake-key', owner='interactive') as budget:
            self.assertEqual(budget.ledger.state['spent'], '0.011877392')
            self.assertEqual(budget.ledger.state['pending'], '0.05')
            ident = budget.ledger.state['reservation']
            with self.assertRaisesRegex(BudgetError, 'owner'):
                budget.settle(ident, '0')
            with self.assertRaises(BudgetError): budget.reserve()
        with EvaluationBudget(self.path, 'fake-key') as evaluation:
            evaluation.settle('0.003', request_id=ident)
            self.assertEqual(evaluation.ledger.state['spent'], '0.014877392')
        self.assertNotIn('fake-key', self.path.read_text())

    def test_multiple_sessions_share_spending_and_receipts(self):
        first, first_local = self.agent('first')
        first.complete([], [], first_local)
        first_ident = first_local.state['shared_request']
        second, second_local = self.agent('second', cost='0.02')
        second.complete([], [], second_local)
        with SharedBudget(self.path, 'fake-key', owner=session_owner(first.session)) as shared:
            self.assertEqual(shared.ledger.state['spent'], '0.03')
            shared.settle(first_ident, '0.01')  # idempotent, never charges twice
            self.assertEqual(shared.ledger.state['spent'], '0.03')
        self.assertEqual(first_local.state['spent'], '0.01')
        self.assertEqual(second_local.state['spent'], '0.02')

    def test_unknown_charge_blocks_other_sessions_and_wrong_reconciliation(self):
        first, local = self.agent('first')
        first.provider.complete.side_effect = TimeoutError('uncertain')
        with self.assertRaises(TimeoutError): first.complete([], [], local)
        ident = local.state['shared_request']
        second, other = self.agent('second')
        with self.assertRaises(BudgetError): second.complete([], [], other)
        second.provider.complete.assert_not_called()
        self.assertIsNone(other.state['pending'])  # proven no dispatch by second session
        with self.assertRaises(BudgetError): reconcile_shared(second, ident, '0')
        self.assertIn('0.02', reconcile_shared(first, ident, '0.02'))
        reconcile_shared(first, ident, '0.02')
        self.assertEqual(local.state['spent'], '0.02')

    def test_low_remaining_or_metadata_failure_release_only_predispatch(self):
        for index, result in enumerate(({'limit_remaining': '1.74'}, {'limit_remaining': None}, RuntimeError('offline'))):
            metadata = Mock(side_effect=result) if isinstance(result, Exception) else Mock(return_value=result)
            agent, local = self.agent(str(index), metadata=metadata)
            with self.assertRaises((BudgetError, RuntimeError)): agent.complete([], [], local)
            agent.provider.complete.assert_not_called()
            self.assertIsNone(local.state['pending'])
            with SharedBudget(self.path, 'fake-key', owner='observer') as shared:
                self.assertEqual(shared.ledger.state['spent'], '0')
                self.assertIsNone(shared.ledger.state['pending'])

    def test_crash_after_shared_settlement_recovers_local_without_rebilling(self):
        agent, local = self.agent()
        original = agent.save
        calls = 0
        def save():
            nonlocal calls
            calls += 1
            if calls == 2:
                raise PersistenceError('simulated crash before local settlement was saved')
            original()
        agent.save = save
        with self.assertRaises(PersistenceError): agent.complete([], [], local)
        agent.session = agent.store.load(agent.session.id)
        self.assertIsNotNone(agent.session.billing['pending'])
        agent.save = original
        budget_status(agent)
        self.assertEqual(agent.session.billing['spent'], '0.01')
        self.assertIsNone(agent.session.billing['pending'])
        with SharedBudget(self.path, 'fake-key', owner='observer') as shared:
            self.assertEqual(shared.ledger.state['spent'], '0.01')
        agent.provider.complete.assert_called_once()

    def test_process_lock_and_unknown_reservation_survive_process_exit(self):
        context = multiprocessing.get_context('spawn')
        started, release, queue = context.Event(), context.Event(), context.Queue()
        process = context.Process(target=reserve_worker, args=(str(self.path), started, release, queue))
        process.start()
        try:
            self.assertTrue(started.wait(10))
            with self.assertRaises(BlockingIOError): SharedBudget(self.path, 'fake-key', owner='another')
            release.set(); process.join(10)
            self.assertEqual(queue.get(timeout=5), 'reserved')
            with SharedBudget(self.path, 'fake-key', owner='another') as shared:
                with self.assertRaises(BudgetError): shared.reserve()
        finally:
            release.set()
            if process.is_alive(): process.terminate()
            process.join(5)
            queue.close()

    def test_new_session_and_restarted_agent_do_not_reset_shared_allowance(self):
        from codine.cli import command
        from codine.workspace import Workspace
        agent, local = self.agent(cost='0.01')
        agent.complete([], [], local)
        previous_id = agent.session.id
        command('/new', agent, Workspace(agent.config.project, lambda _: False), Mock())
        self.assertNotEqual(agent.session.id, previous_id)
        agent.session.billing = Budget.create().state
        agent.complete([], [], Budget(agent.session.billing))
        resumed = agent.store.load(agent.session.id)
        replacement = Agent(agent.config, agent.provider, None, agent.store, resumed, Mock(), Redactor())
        replacement.configure_shared_budget(self.path, Mock(return_value={'limit_remaining': 5}))
        replacement.complete([], [], Budget(resumed.billing))
        with SharedBudget(self.path, 'fake-key', owner='observer') as shared:
            self.assertEqual(shared.ledger.state['spent'], '0.03')
            self.assertEqual(len(shared.ledger.state['records']), 3)

    def test_fresh_import_style_session_cannot_bypass_exhausted_shared_budget(self):
        with SharedBudget(self.path, 'fake-key', owner='prior-session', request_cap='3.50') as shared:
            ident = shared.reserve(); shared.dispatched(ident); shared.settle(ident, '3.40')
        for name in ('new', 'imported'):
            agent, local = self.agent(name)
            with self.assertRaises(BudgetError): agent.complete([], [], local)
            agent.provider.complete.assert_not_called()
        with SharedBudget(self.path, 'fake-key', owner='observer') as shared:
            self.assertEqual(shared.ledger.state['spent'], '3.40')

    def test_imported_pending_reference_cannot_reconcile_original_owner(self):
        import copy
        first, local = self.agent('first')
        first.provider.complete.side_effect = TimeoutError('unknown')
        with self.assertRaises(TimeoutError): first.complete([], [], local)
        imported, _ = self.agent('imported')
        imported.session.billing = copy.deepcopy(local.state)
        with self.assertRaisesRegex(BudgetError, 'owner'):
            reconcile_shared(imported, local.state['shared_request'], '0')
        imported.provider.complete.assert_not_called()

    def test_orphan_local_intent_recovers_only_with_same_durable_ledger_identity(self):
        import uuid
        agent, local = self.agent()
        with SharedBudget(self.path, 'fake-key', owner=session_owner(agent.session)) as shared:
            local.reserve()
            local.state.update(shared_request=uuid.uuid4().hex, shared_ledger=shared.ledger.state['ledger_id'])
            agent.save()
        budget_status(agent)
        self.assertIsNone(local.state['pending'])
        self.assertEqual(local.state['spent'], '0')
        local.reserve()
        local.state.update(shared_request=uuid.uuid4().hex, shared_ledger=uuid.uuid4().hex)
        with self.assertRaisesRegex(BudgetError, 'another allowance'):
            budget_status(agent)
        self.assertIsNotNone(local.state['pending'])

    def test_changed_key_does_not_create_a_fresh_allowance(self):
        with SharedBudget(self.path, 'fake-key', owner='one') as shared:
            ident = shared.reserve(); shared.dispatched(ident); shared.settle(ident, '.01')
        with self.assertRaisesRegex(BudgetError, 'key changed'):
            SharedBudget(self.path, 'other-fake-key', owner='two')
