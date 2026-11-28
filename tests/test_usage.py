import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from codine.sessions import Session, SessionStore
from codine.usage import record, describe, validate


class UsageTests(unittest.TestCase):
    def test_missing_zero_and_subset_counts_are_distinct(self):
        state = {}
        record(state, SimpleNamespace(prompt_tokens=20, completion_tokens=10, total_tokens=30,
               prompt_tokens_details=SimpleNamespace(cached_tokens=0),
               completion_tokens_details=SimpleNamespace(reasoning_tokens=6)))
        record(state, {'prompt_tokens': 8, 'completion_tokens': 2})
        record(state, None)
        validate(state)
        self.assertEqual(state['totals']['total'], 30)
        self.assertEqual(state['totals']['output'], 12)
        self.assertEqual(state['reported']['cache_read'], 1)
        self.assertIsNone(state['last']['cache_read'])
        report = describe(state)
        self.assertIn('cache_read: last unknown; reported sum 0 (1/3 responses)', report)
        self.assertIn('reasoning: last unknown; reported sum 6 (1/3 responses)', report)

    def test_invalid_provider_numbers_are_unknown(self):
        state = {}
        record(state, {'prompt_tokens': True, 'completion_tokens': -1, 'total_tokens': '22'})
        self.assertTrue(all(value is None for value in state['last'].values()))
        self.assertIn('reported sum unknown', describe(state))
        invalid = copy.deepcopy(state)
        invalid['reported']['input'] = 2
        with self.assertRaises(ValueError): validate(invalid)

    def test_resume_and_legacy_loading(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = SessionStore(root)
            self.addCleanup(store.close)
            session = Session.create(root, 'offline', 'system')
            record(session.usage_details, {'prompt_tokens': 10})
            store.save(session)
            self.assertEqual(store.load(session.id).usage_details, session.usage_details)
            path = store.path(session.id)
            data = json.loads(path.read_text())
            del data['usage_details']
            path.write_text(json.dumps(data))
            self.assertEqual(store.load(session.id).usage_details, {})
            data['usage_details'] = {'requests': True}
            path.write_text(json.dumps(data))
            with self.assertRaises(ValueError): store.load(session.id)


class UsageAgentTests(unittest.TestCase):
    def setUp(self):
        from unittest.mock import Mock
        from codine.agent import Agent
        from codine.config import Config, Redactor
        temporary=tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name).resolve()
        self.store=SessionStore(self.root); self.addCleanup(self.store.close)
        self.session=Session.create(self.root,'fake','system',provider='openrouter')
        self.provider=Mock()
        self.agent=Agent(Config(self.root,provider='openrouter',api_key='fake'),self.provider,
                         Mock(),self.store,self.session,Mock(),Redactor())

    def response(self, usage):
        self.provider.complete.return_value=({'role':'assistant','content':'done'},'stop',usage)

    def test_completed_dict_sdk_and_missing_counts_persist_once(self):
        from openai.types.completion_usage import CompletionUsage
        self.response({'prompt_tokens':4,'completion_tokens':2,'total_tokens':6,
                       'prompt_tokens_details':{'cached_tokens':0}})
        self.agent.complete([],[],None)
        self.response(CompletionUsage(prompt_tokens=10,completion_tokens=5,total_tokens=15,
            completion_tokens_details={'reasoning_tokens':3}))
        self.agent.complete([],[],None)
        self.response(None)
        self.agent.complete([],[],None)
        saved=self.store.load(self.session.id)
        self.assertEqual(saved.usage,{'input':14,'output':7,'total':21})
        self.assertEqual(saved.usage_details['requests'],3)
        self.assertEqual(saved.usage_details['reported']['total'],2)
        self.assertEqual(saved.usage_details['totals']['reasoning'],3)
        self.assertIsNone(saved.usage_details['last']['total'])

    def test_missing_cost_and_overrun_record_tokens_without_resetting_budget(self):
        from unittest.mock import Mock
        from codine.budget import Budget, BudgetError
        for shared in (False,True):
            for cost in (None,.30):
                with self.subTest(shared=shared,cost=cost):
                    session=Session.create(self.root,'fake','system',provider='openrouter')
                    self.agent.session=session
                    budget=Budget.create(); session.billing=budget.state
                    ledger=self.root/(session.id+'-budget.json')
                    if shared: self.agent.configure_shared_budget(ledger,Mock(return_value={'limit_remaining':5}))
                    elif hasattr(self.agent,'shared_budget_path'): del self.agent.shared_budget_path
                    usage=SimpleNamespace(prompt_tokens=7,completion_tokens=3,total_tokens=10)
                    if cost is not None: usage.cost=cost
                    self.response(usage)
                    with self.assertRaises(BudgetError): self.agent.complete([],[],budget)
                    saved=self.store.load(session.id)
                    self.assertEqual(saved.usage_details['requests'],1)
                    self.assertEqual(saved.usage['total'],10)
                    if cost is None:
                        self.assertEqual(saved.billing['pending'],'0.25')
                        if shared: self.assertIsNotNone(json.loads(ledger.read_text())['pending'])
                    else:
                        self.assertEqual(saved.billing['spent'],'0.3')
                        self.assertTrue(saved.billing['blocked'])
                        self.assertIsNone(saved.billing['pending'])

    def test_interruption_and_rejection_never_infer_completed_usage(self):
        for error in (KeyboardInterrupt(),RuntimeError('rejected')):
            self.provider.complete.side_effect=error
            with self.assertRaises(type(error)): self.agent.complete([],[],None)
            self.assertEqual(self.session.usage_details,{})
            self.assertEqual(self.session.usage['total'],0)

    def test_save_rejects_malformed_details_before_replacing_archive(self):
        self.store.save(self.session)
        before=self.store.path(self.session.id).read_bytes()
        self.session.usage_details={'requests':False}
        with self.assertRaises(ValueError): self.store.save(self.session)
        self.assertEqual(self.store.path(self.session.id).read_bytes(),before)

    def test_usage_save_failure_leaves_charge_unsettled(self):
        from unittest.mock import patch
        from codine.agent import PersistenceError
        from codine.budget import Budget
        budget=Budget.create(); self.session.billing=budget.state
        self.response(SimpleNamespace(cost=.01,total_tokens=5))
        original=self.store.save
        def save(session):
            if session.usage_details:
                raise OSError('disk unavailable')
            original(session)
        with patch.object(self.store,'save',side_effect=save):
            with self.assertRaises(PersistenceError): self.agent.complete([],[],budget)
        saved=self.store.load(self.session.id)
        self.assertEqual(saved.billing['pending'],'0.25')
        self.assertEqual(saved.billing['spent'],'0')
        self.provider.complete.assert_called_once()
