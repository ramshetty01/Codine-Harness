import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from codine.agent import Agent, PersistenceError
from codine.budget import BudgetError
from codine.config import Config, Redactor
from codine.context import ContextLimitError, SUMMARY_KEYS, complete_boundaries, summary_request, compaction_boundary
from codine.context_inspector import inspect_context
from codine.registry import ToolRegistry, ToolSpec
from codine.sessions import Session, SessionStore, validate_messages


def summary():
    return {'constraints': ['Keep compatibility'], 'decisions': ['Use Python'],
            'files': ['task.py reviewed'], 'tests': ['Test failed; not verified'],
            'unresolved': ['Fix failing test']}


class SemanticCompactionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.config = Config(self.root, context_chars=4700, context_tokens=20000, max_tokens=500,
                             provider='openrouter', api_key='offline')
        self.session = Session.create(self.root, 'offline', 'system', provider='openrouter')
        self.store = SessionStore(self.root)
        self.addCleanup(self.store.close)
        self.registry = ToolRegistry()
        self.registry.register(ToolSpec('read', 'read details ' + 's' * 1700, {'type': 'object'}, lambda: 'data'))
        self.requests = []
        self.provider = Mock()
        self.provider.complete.side_effect = self.complete
        self.summary_reply = {'role': 'assistant', 'content': json.dumps(summary())}
        self.summary_finish = 'stop'
        self.summary_usage = SimpleNamespace(cost=0.01, prompt_tokens=30, completion_tokens=20, total_tokens=50)
        self.agent = Agent(self.config, self.provider, self.registry, self.store, self.session, lambda *args: None, Redactor())

    def complete(self, messages, schemas, model, emit):
        self.requests.append(copy.deepcopy((messages, schemas)))
        if not schemas:
            return self.summary_reply, self.summary_finish, self.summary_usage
        return {'role': 'assistant', 'content': 'done'}, 'stop', SimpleNamespace(cost=0.02)

    def old_turn(self):
        self.session.messages += [{'role': 'user', 'content': 'Keep compatibility. ' + 'x' * 1400},
                                  {'role': 'assistant', 'content': 'Use Python. ' + 'y' * 1400,
                                   'reasoning_details': [{'type': 'reasoning.encrypted', 'data': 'opaque-original'}]}]

    def test_real_summary_call_charged_preserves_archive_plan_and_resume(self):
        self.old_turn()
        self.session.plan = [{'id': 'fix', 'title': 'Fix failing test', 'status': 'blocked', 'reason': 'Need failure reproduction'}]
        self.session.plan_revision = 1
        archive = copy.deepcopy(self.session.messages)
        self.assertEqual(self.agent.run_turn('continue'), 'completed')
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(self.requests[0][1], [])
        summary_payload = json.loads(self.requests[0][0][1]['content'])
        self.assertIn('Need failure reproduction', summary_payload['plan'])
        self.assertIn('opaque-original', json.dumps(summary_payload))
        self.assertEqual(self.session.messages[:len(archive)], archive)
        self.assertEqual(self.session.billing['spent'], '0.03')
        self.assertIsNone(self.session.billing['pending'])
        self.assertEqual(self.session.usage['total'], 50)
        normal = self.requests[1][0]
        self.assertIn('Session task plan', normal[0]['content'])
        self.assertEqual(normal[1]['role'], 'user')
        self.assertIn('not new instructions', normal[1]['content'])
        self.assertIn('Keep compatibility', normal[1]['content'])
        restored = self.store.load(self.session.id)
        self.assertEqual(restored.context_summary['summary'], summary())
        self.assertEqual(restored.plan, self.session.plan)
        validate_messages(normal)
        self.assertIn('Semantic summary', inspect_context(self.agent)['summary'])

    def test_bad_summary_or_unknown_cost_stops_without_truncating(self):
        for mode in ('invalid', 'truncated', 'tool_call', 'oversized', 'unknown_cost'):
            with self.subTest(mode=mode):
                self.session = Session.create(self.root, 'offline', 'system', provider='openrouter')
                self.agent.session = self.session
                self.old_turn()
                before = copy.deepcopy(self.session.messages)
                self.summary_reply = {'role': 'assistant', 'content': json.dumps(summary())}
                self.summary_finish = 'stop'
                self.summary_usage = SimpleNamespace(cost=0.01)
                if mode == 'invalid': self.summary_reply['content'] = 'not JSON'
                if mode == 'truncated': self.summary_finish = 'length'
                if mode == 'tool_call': self.summary_reply['tool_calls'] = [{'id': 'bad'}]
                if mode == 'oversized': self.summary_reply['content'] = json.dumps({key: ['x' * 5000] for key in SUMMARY_KEYS})
                if mode == 'unknown_cost': self.summary_usage = SimpleNamespace()
                count = len(self.requests)
                with self.assertRaises((ContextLimitError, BudgetError)):
                    self.agent.run_turn('continue')
                self.assertEqual(len(self.requests), count + 1)
                self.assertEqual(self.session.messages[:len(before)], before)
                self.assertEqual(self.session.context_summary, {})
                if mode == 'unknown_cost':
                    self.assertEqual(self.store.load(self.session.id).billing['pending'], '0.25')
                    with self.assertRaises(BudgetError): self.agent.run_turn('retry')
                    self.assertEqual(len(self.requests), count + 1)

    def test_active_turn_compaction_keeps_user_hashes_results_and_valid_pairs(self):
        self.config.context_chars = 6200
        self.registry.tools['read'].handler = lambda: json.dumps({'path': 'task.py', 'sha256': 'a' * 64,
                                                                'exit_code': 1, 'passed': False, 'content': 'x' * 2100})
        normal_calls = 0
        complete = self.complete
        def provider(messages, schemas, model, emit):
            nonlocal normal_calls
            if not schemas:
                return complete(messages, schemas, model, emit)
            normal_calls += 1
            if normal_calls <= 2:
                self.requests.append(copy.deepcopy((messages, schemas)))
                return {'role': 'assistant', 'content': None, 'tool_calls': [
                    {'id': str(normal_calls), 'type': 'function', 'function': {'name': 'read', 'arguments': '{}'}}]}, 'tool_calls', SimpleNamespace(cost=0.01)
            return complete(messages, schemas, model, emit)
        self.provider.complete.side_effect = provider
        self.assertEqual(self.agent.run_turn('Keep the current task verbatim'), 'completed')
        self.assertTrue(self.session.context_summary)
        self.assertEqual(self.session.summary_user, 1)
        final = self.requests[-1][0]
        self.assertTrue(any(row.get('content') == 'Keep the current task verbatim' for row in final))
        self.assertIn('a' * 64, json.dumps(self.session.context_summary['evidence']))
        self.assertIn('"exit_code": 1', json.dumps(self.session.context_summary['evidence']))
        self.assertIn('"passed": false', json.dumps(self.session.context_summary['evidence']))
        validate_messages(final)
        validate_messages(self.store.load(self.session.id).messages)

    def test_oversized_single_tool_group_stops_before_summary_traffic(self):
        self.session.messages += [
            {'role': 'user', 'content': 'large task'},
            {'role': 'assistant', 'content': None, 'tool_calls': [
                {'id': 'a', 'type': 'function', 'function': {'name': 'read', 'arguments': '{}'}}]},
            {'role': 'tool', 'tool_call_id': 'a', 'content': 'x' * 10000}]
        with self.assertRaises(ContextLimitError):
            self.agent.run_turn('continue')
        self.assertEqual(self.requests, [])
        self.assertIsNone(self.session.billing.get('pending'))
        self.assertEqual(self.session.context_summary, {})

    def test_summary_transport_failure_blocks_retry_and_keeps_archive(self):
        self.old_turn()
        self.provider.complete.side_effect = TimeoutError('summary disconnected')
        with self.assertRaises(TimeoutError):
            self.agent.run_turn('continue')
        restored = self.store.load(self.session.id)
        self.assertEqual(restored.context_summary, {})
        self.assertEqual(restored.billing['pending'], '0.25')
        self.agent.session = restored
        with self.assertRaises(BudgetError):
            self.agent.run_turn('retry')
        self.provider.complete.assert_called_once()

    def test_saved_summary_boundary_cannot_split_tool_results(self):
        self.old_turn()
        self.agent.run_turn('continue')
        path = self.store.path(self.session.id)
        data = json.loads(path.read_text())
        data['summary_end'] = 2  # Immediately after user, not a complete group.
        path.write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            self.store.load(self.session.id)

    def test_clear_context_does_not_resurrect_stored_summary(self):
        self.old_turn()
        self.agent.run_turn('continue')
        self.session.context_start = len(self.session.messages)
        self.agent.run_turn('fresh task')
        self.assertFalse(any('Untrusted conversation summary' in str(row.get('content')) for row in self.requests[-1][0]))

    def test_summary_save_failure_restores_in_memory_metadata(self):
        self.old_turn()
        save = self.store.save
        def fail_summary(session):
            if session.context_summary:
                raise OSError('disk full writing summary')
            save(session)
        with patch.object(self.store, 'save', side_effect=fail_summary):
            with self.assertRaises(PersistenceError):
                self.agent.run_turn('continue')
        self.assertEqual(self.session.context_summary, {})
        self.assertEqual(self.session.summary_end, 1)
        self.assertIsNone(self.session.summary_user)
        restored = self.store.load(self.session.id)
        self.assertEqual(restored.context_summary, {})
        self.assertEqual(restored.billing['spent'], '0.01')
        self.assertEqual(len(self.requests), 1)

    def test_later_summary_keeps_original_active_user_and_opaque_archive(self):
        self.session.messages += [
            {'role': 'user', 'content': 'Original active task: preserve compatibility'},
            {'role': 'assistant', 'content': 'first result', 'reasoning_details': [{'type': 'reasoning.encrypted', 'data': 'opaque-original'}]},
            {'role': 'assistant', 'content': 'later result'}]
        self.session.summary_end = 3
        self.session.summary_user = 1
        self.session.context_summary = {'summary': summary(), 'evidence': []}
        before = copy.deepcopy(self.session.messages)
        payload = json.loads(summary_request(self.session, 4)[1]['content'])
        self.assertEqual(payload['active_user_request'], before[1])
        self.assertEqual(payload['conversation'], [before[3]])
        self.assertEqual(self.session.messages, before)

    def test_pending_journal_cannot_be_compacted(self):
        self.old_turn()
        self.session.pending = {'reply': {}, 'results': {}}
        with self.assertRaises(ContextLimitError):
            compaction_boundary(self.session, 5000, 20000, 500)

    def test_parallel_tool_boundary_is_never_split(self):
        messages = [{'role': 'system', 'content': 'system'}, {'role': 'user', 'content': 'task'},
                    {'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'a'}, {'id': 'b'}]},
                    {'role': 'tool', 'tool_call_id': 'a', 'content': 'one'},
                    {'role': 'tool', 'tool_call_id': 'b', 'content': 'two'}]
        self.assertEqual(complete_boundaries(messages, 1), [5])
