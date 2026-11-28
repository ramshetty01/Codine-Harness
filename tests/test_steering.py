import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from codine.agent import Agent, PersistenceError
from codine.config import Config, Redactor
from codine.inbox import Inbox, MAX_ITEMS
from codine.registry import ToolRegistry, ToolSpec
from codine.sessions import Session, SessionStore, validate_messages


def call(ident):
    return {'id': ident, 'type': 'function', 'function': {'name': 'action', 'arguments': '{}'}}


def answer(text='done'):
    return {'role': 'assistant', 'content': text}, 'stop', None


class SteeringTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.store = SessionStore(self.root)
        self.addCleanup(self.store.close)
        self.session = Session.create(self.root, 'offline', 'system')
        self.store.save(self.session)
        self.inbox = Inbox(self.store, self.session)
        self.provider = Mock()
        self.provider.complete.return_value = answer()
        self.registry = ToolRegistry()
        self.agent = Agent(Config(self.root), self.provider, self.registry, self.store, self.session,
                           Mock(), Redactor())
        self.agent.inbox = self.inbox

    def test_steering_during_request_skips_stale_batch_and_reaches_next_request(self):
        executed = []
        self.registry.register(ToolSpec('action', 'action', {'type': 'object'}, lambda: executed.append(1)))
        def first(*args):
            self.inbox.add('Do not write; explain only', 'steer')
            return {'role': 'assistant', 'content': None, 'tool_calls': [call('a'), call('b'), call('c')]}, 'tool_calls', None
        def complete(*args):
            if self.provider.complete.call_count == 1:
                return first(*args)
            messages = args[0]
            validate_messages(messages)
            self.assertEqual(messages[-1]['content'], 'Do not write; explain only')
            self.assertTrue(all(json.loads(row['content'])['status'] == 'skipped' for row in messages if row['role'] == 'tool'))
            return answer()
        self.provider.complete.side_effect = complete
        self.assertEqual(self.agent.run_turn('write two things'), 'completed')
        self.assertEqual(executed, [])
        self.assertEqual(self.inbox.list(), [])
        validate_messages(self.store.load(self.session.id).messages)

    def test_inflight_action_finishes_once_then_remaining_batch_skipped(self):
        executed = []
        def action():
            executed.append('effect')
            self.inbox.add('Change direction', 'steer')
            return {'ok': True}
        self.registry.register(ToolSpec('action', 'action', {'type': 'object'}, action))
        self.provider.complete.side_effect = [({'role': 'assistant', 'content': None, 'tool_calls': [call('a'), call('b')]}, 'tool_calls', None), answer()]
        self.agent.run_turn('work')
        self.assertEqual(executed, ['effect'])
        results = [json.loads(row['content']) for row in self.session.messages if row['role'] == 'tool']
        self.assertTrue(results[0]['ok'])
        self.assertEqual(results[1]['status'], 'skipped')
        validate_messages(self.session.messages)

    def test_followup_stays_separate_and_can_be_removed(self):
        item = self.inbox.add('Later task')
        self.agent.run_turn('Current task')
        self.assertEqual(len(self.inbox.list()), 1)
        self.assertNotIn('Later task', json.dumps(self.provider.complete.call_args.args[0]))
        self.inbox.remove(item['id'][:8])
        with self.assertRaisesRegex(ValueError, 'removed'):
            self.agent.run_turn(item['text'], queued_item=item)
        self.assertEqual(self.provider.complete.call_count, 1)

    def test_ack_failure_and_restart_do_not_duplicate_delivered_user_message(self):
        item = self.inbox.add('Deliver once')
        with patch.object(self.inbox, 'reconcile', side_effect=OSError('disk failed after receipt')):
            with self.assertRaises(OSError):
                self.agent.run_turn(item['text'], queued_item=item)
        restored = self.store.load(self.session.id)
        recovered = Inbox(self.store, restored)
        self.assertEqual(recovered.list(), [])
        self.assertEqual(sum(row.get('content') == 'Deliver once' for row in restored.messages), 1)
        self.provider.complete.assert_not_called()

    def test_session_save_failure_keeps_item_unacknowledged(self):
        item = self.inbox.add('Keep this task')
        with patch.object(self.store, 'save', side_effect=OSError('disk full')):
            with self.assertRaises(PersistenceError):
                self.agent.run_turn(item['text'], queued_item=item)
        self.assertNotIn(item['id'], self.session.queue_receipts)
        restored = self.store.load(self.session.id)
        self.assertNotIn(item['id'], restored.queue_receipts)
        self.assertEqual(Inbox(self.store, restored).list(), [item])
        self.provider.complete.assert_not_called()

    def test_cancel_during_partial_stream_does_not_execute_or_accept_partial_calls(self):
        followup = self.inbox.add('later')
        def partial(messages, schemas, model, emit):
            self.inbox.cancel.set()
            emit('text', 'partial')
            raise AssertionError('callback should interrupt')
        self.provider.complete.side_effect = partial
        with self.assertRaises(KeyboardInterrupt):
            self.agent.run_turn('work')
        self.assertEqual(self.inbox.list(), [followup])
        self.assertIsNone(self.session.pending)
        self.assertEqual(self.session.last_run['reason'], 'user_cancelled')
        validate_messages(self.store.load(self.session.id).messages)

    def test_cancel_after_action_preserves_result_and_never_replays_next(self):
        done = []
        def action():
            done.append(1)
            self.inbox.cancel.set()
            return {'status': 'cancelled', 'exit_code': -15}
        self.registry.register(ToolSpec('action', 'action', {'type': 'object'}, action))
        self.provider.complete.return_value = ({'role': 'assistant', 'content': None, 'tool_calls': [call('a'), call('b')]}, 'tool_calls', None)
        with self.assertRaises(KeyboardInterrupt):
            self.agent.run_turn('work')
        self.assertEqual(done, [1])
        restored = self.store.load(self.session.id)
        results = [json.loads(row['content']) for row in restored.messages if row['role'] == 'tool']
        self.assertEqual(results[0]['status'], 'cancelled')
        self.assertIn('NOT replayed', results[1]['error'])
        validate_messages(restored.messages)

    def test_bounds_redaction_and_invalid_state_fail_closed(self):
        self.inbox.redactor = Redactor(['secret-value'])
        self.assertNotIn('secret-value', self.inbox.add('secret-value text')['text'])
        for i in range(MAX_ITEMS - 1):
            self.inbox.add(str(i))
        with self.assertRaisesRegex(ValueError, 'full'):
            self.inbox.add('overflow')
        with self.assertRaises(ValueError):
            self.inbox.add('x' * 16001)
        self.inbox.path.write_text('[{"bad":true}]')
        with self.assertRaises(ValueError):
            Inbox(self.store, self.session)

    def test_recovered_or_stopped_queue_requires_explicit_resume(self):
        item = self.inbox.add('Recovered correction', 'steer')
        recovered = Inbox(self.store, self.session)
        self.agent.inbox = recovered
        self.agent.run_turn('A new unrelated question')
        self.assertEqual(recovered.ready('steer'), [])
        self.assertEqual(recovered.list(), [item])
        self.assertNotIn(item['text'], json.dumps(self.provider.complete.call_args.args[0]))
        recovered.resume()
        self.agent.run_turn(item['text'], queued_item=item)
        self.assertEqual(recovered.list(), [])
        self.assertEqual(sum(row.get('content') == item['text'] for row in self.session.messages), 1)
        calls = self.provider.complete.call_count
        with self.assertRaisesRegex(ValueError, 'already delivered'):
            self.agent.run_turn(item['text'], queued_item=item)
        self.assertEqual(self.provider.complete.call_count, calls)

    def test_queue_write_failure_stops_delivery_without_replacing_saved_queue(self):
        item = self.inbox.add('preserve me')
        with patch('codine.inbox.os.replace', side_effect=OSError('no space')):
            with self.assertRaises(OSError):
                self.inbox.add('must not claim queued')
        self.assertEqual(self.inbox.list(), [item])
        self.assertEqual(json.loads(self.inbox.path.read_text()), [item])
        with self.assertRaisesRegex(RuntimeError, 'queue save failed'):
            self.agent.run_turn('do not run')
        self.provider.complete.assert_not_called()

    def test_unknown_provider_cost_blocks_and_preserves_followup(self):
        from codine.budget import BudgetError
        self.agent.config.provider = 'openrouter'
        self.agent.config.api_key = 'offline-placeholder'
        item = self.inbox.add('later')
        with self.assertRaises(BudgetError):
            self.agent.run_turn('work')
        self.assertIsNotNone(self.session.billing['pending'])
        self.assertEqual(self.inbox.list(), [item])
        self.assertEqual(self.provider.complete.call_count, 1)
        with self.assertRaises(BudgetError):
            self.agent.run_turn('try again')
        self.assertEqual(self.provider.complete.call_count, 1)
