import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock, patch

from codine.agent import Agent, PersistenceError
from codine.budget import Budget, BudgetError
from codine.cli import main
from codine.config import Config, Redactor
from codine.context import ContextLimitError
from codine.registry import ToolRegistry, ToolSpec
from codine.sessions import Session, SessionStore, validate_messages
from codine.terminal import Terminal
from codine.tools import build_registry
from codine.workspace import Workspace


class RunStateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.config = Config(self.root)
        self.session = Session.create(self.root, 'offline', 'system')
        self.store = SessionStore(self.root)
        self.addCleanup(self.store.close)
        self.registry = ToolRegistry()
        self.provider = Mock()
        self.provider.complete.return_value = ({'role': 'assistant', 'content': 'all done'}, 'stop', None)
        self.events = []
        self.agent = Agent(self.config, self.provider, self.registry, self.store, self.session,
                           lambda kind, data: self.events.append((kind, data)), Redactor())

    def terminal_record(self):
        records = [data for kind, data in self.events if kind == 'run_end']
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['verification'], 'unverified')
        return records[0]

    def test_model_stop_does_not_complete_unresolved_or_stale_plan(self):
        self.session.plan = [{'id': 'todo', 'title': 'Fix tests', 'status': 'pending'}]
        self.assertEqual(self.agent.run_turn('do work'), 'completed')
        record = self.terminal_record()
        self.assertEqual((record['state'], record['reason'], record['task_state']),
                         ('awaiting_input', 'model_stop', 'unresolved'))
        self.events.clear()
        self.session.plan = [{'id': 'todo', 'title': 'Fix tests', 'status': 'completed', 'evidence': 'Prior result'}]
        self.session.plan_revision = 1
        self.agent.run_turn('a new request')
        self.assertEqual(self.terminal_record()['task_state'], 'stale_plan')
        self.assertEqual(self.store.load(self.session.id).last_run['task_state'], 'stale_plan')

    def test_plan_completed_during_turn_is_only_reported_complete(self):
        def complete(*args):
            self.session.plan = [{'id': 'todo', 'title': 'Fix tests', 'status': 'completed', 'evidence': 'Tool output claimed pass'}]
            self.session.plan_revision += 1
            return {'role': 'assistant', 'content': 'done'}, 'stop', None
        self.provider.complete.side_effect = complete
        self.agent.run_turn('work')
        self.assertEqual(self.terminal_record()['task_state'], 'reported_complete')

    def test_limits_restart_and_blocked_plan_keep_explicit_states(self):
        for outcome, state, retryable in [('output_limit', 'paused', True), ('step_limit', 'paused', True),
                                          ('loop_limit', 'paused', False), ('tool_limit', 'paused', False), ('restart', 'restarting', True)]:
            self.events.clear()
            with patch.object(self.agent, '_run_turn', return_value=outcome):
                self.assertEqual(self.agent.run_turn('task'), outcome)
            record = self.terminal_record()
            self.assertEqual(record['state'], state)
            self.assertEqual(record['retryable'], retryable)
        self.session.plan = [{'id': 'todo', 'title': 'Fix tests', 'status': 'blocked', 'reason': 'Need user decision'}]
        self.events.clear()
        self.agent.run_turn('work')
        self.assertEqual(self.terminal_record()['task_state'], 'blocked')

    def test_provider_failure_persists_once_and_never_retries(self):
        failure = TimeoutError('provider disconnected')
        self.provider.complete.side_effect = failure
        with self.assertRaises(TimeoutError) as caught:
            self.agent.run_turn('work')
        self.assertIs(caught.exception, failure)
        record = self.terminal_record()
        self.assertEqual(record['reason'], 'provider_error')
        self.assertFalse(record['retryable'])
        self.assertEqual(self.store.load(self.session.id).last_run['reason'], 'provider_error')
        self.provider.complete.assert_called_once()

    def test_context_and_pending_budget_have_distinct_block_reasons(self):
        self.config.context_chars = 1
        with self.assertRaises(ContextLimitError):
            self.agent.run_turn('too large')
        self.assertEqual(self.terminal_record()['reason'], 'context_limit')
        self.provider.complete.assert_not_called()
        self.events.clear()
        self.config.context_chars = 160000
        self.config.provider = 'openrouter'
        self.config.api_key = 'offline'
        self.session.billing = Budget.create('1', '0.25').state
        Budget(self.session.billing).reserve()
        with self.assertRaises(BudgetError):
            self.agent.run_turn('retry')
        record = self.terminal_record()
        self.assertEqual(record['reason'], 'budget_blocked')
        self.assertTrue(record['pending_charge'])
        self.assertFalse(record['retryable'])

    def test_primary_save_failure_is_not_retried(self):
        with patch.object(self.store, 'save', side_effect=OSError('full')) as save:
            with self.assertRaises(PersistenceError):
                self.agent.run_turn('work')
        save.assert_called_once()
        self.provider.complete.assert_not_called()
        record = self.terminal_record()
        self.assertEqual(record['reason'], 'persistence_error')
        self.assertEqual(record['persistence'], 'not_attempted')

    def test_secondary_save_failure_never_masks_original_provider_error(self):
        failure = TimeoutError('provider disconnected')
        self.provider.complete.side_effect = failure
        save = self.store.save
        calls = 0
        def failing_save(session):
            nonlocal calls
            calls += 1
            if calls > 1: raise OSError('full during failure recording')
            save(session)
        with patch.object(self.store, 'save', side_effect=failing_save):
            with self.assertRaises(TimeoutError) as caught:
                self.agent.run_turn('work')
        self.assertIs(caught.exception, failure)
        self.assertEqual(calls, 2)
        self.assertEqual(self.terminal_record()['persistence'], 'failed')

    def test_tool_or_approval_cancellation_repairs_once_and_skips_remaining_actions(self):
        effects = []
        def cancel():
            effects.append('first')
            raise KeyboardInterrupt()
        self.registry.register(ToolSpec('cancel', '', {'type': 'object'}, cancel))
        self.registry.register(ToolSpec('second', '', {'type': 'object'}, lambda: effects.append('second')))
        calls = [{'id': name, 'type': 'function', 'function': {'name': name, 'arguments': '{}'}} for name in ('cancel', 'second')]
        self.provider.complete.return_value = ({'role': 'assistant', 'content': None, 'tool_calls': calls}, 'tool_calls', None)
        with self.assertRaises(KeyboardInterrupt):
            self.agent.run_turn('work')
        self.assertEqual(effects, ['first'])
        record = self.terminal_record()
        self.assertEqual(record['state'], 'cancelled')
        self.assertTrue(record['unknown_actions'])
        self.assertFalse(record['retryable'])
        restored = self.store.load(self.session.id)
        validate_messages(restored.messages)
        self.assertIsNone(restored.pending)
        self.assertIn('NOT replayed', restored.messages[-1]['content'])

    def test_real_approval_interrupt_prevents_rest_of_tool_batch(self):
        terminal = Terminal(Redactor(), interactive=False)
        terminal.interactive = True
        terminal.line = Mock()
        terminal.panel = Mock()
        workspace = Workspace(self.root, terminal.approve, lambda *args: None)
        self.agent.registry = build_registry(workspace, lambda: None)
        calls = [{'id': name, 'type': 'function', 'function': {'name': 'write_file',
                  'arguments': json.dumps({'path': name + '.txt', 'content': 'must not write'})}}
                 for name in ('first', 'second')]
        self.provider.complete.return_value = ({'role': 'assistant', 'content': None, 'tool_calls': calls}, 'tool_calls', None)
        with patch('codine.terminal.PromptSession') as editor:
            editor.return_value.prompt.side_effect = KeyboardInterrupt()
            with self.assertRaises(KeyboardInterrupt):
                self.agent.run_turn('write two files')
        editor.return_value.prompt.assert_called_once()
        self.assertFalse((self.root / 'first.txt').exists())
        self.assertFalse((self.root / 'second.txt').exists())
        self.assertEqual(self.terminal_record()['reason'], 'user_cancelled')
        validate_messages(self.store.load(self.session.id).messages)

    def test_summary_cancellation_keeps_unknown_charge_and_single_end(self):
        self.config.provider = 'openrouter'
        self.config.api_key = 'offline'
        self.config.context_chars = 4700
        self.config.max_tokens = 500
        self.registry.register(ToolSpec('read', 's' * 1700, {'type': 'object'}, lambda: None))
        self.session.messages += [{'role': 'user', 'content': 'x' * 1400}, {'role': 'assistant', 'content': 'y' * 1400}]
        self.provider.complete.side_effect = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.agent.run_turn('continue')
        self.assertEqual(self.provider.complete.call_args.args[1], [])
        record = self.terminal_record()
        self.assertEqual(record['reason'], 'user_cancelled')
        self.assertTrue(record['pending_charge'])
        self.assertEqual(self.session.context_summary, {})

    def test_json_cli_emits_one_failure_end_and_terminal_renders_reason(self):
        self.provider.complete.side_effect = TimeoutError('network failure')
        output = io.StringIO()
        with patch('codine.cli.NvidiaProvider', return_value=self.provider), redirect_stdout(output):
            result = main(['--provider', 'nvidia', '--project', str(self.root), '--json', '--prompt', 'hello'])
        self.assertEqual(result, 1)
        ends = [row for row in map(json.loads, output.getvalue().splitlines()) if row['event'] == 'run_end']
        self.assertEqual(len(ends), 1)
        self.assertEqual(ends[0]['data']['reason'], 'provider_error')
        output = io.StringIO()
        with redirect_stdout(output):
            Terminal(Redactor(), interactive=False).emit('run_end', ends[0]['data'])
        self.assertIn('provider error', output.getvalue())

    def test_saved_state_cannot_claim_verified_success(self):
        self.agent.run_turn('work')
        path = self.store.path(self.session.id)
        data = json.loads(path.read_text())
        data['last_run']['verification'] = 'verified'
        path.write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            self.store.load(self.session.id)
