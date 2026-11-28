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
