"""Deterministic failure boundaries; no provider requests or paid evaluations."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

import httpx
from openai import APITimeoutError, APIConnectionError, RateLimitError
from codine.agent import Agent, PersistenceError
from codine.budget import BudgetError
from codine.config import Config, Redactor
from codine.provider import collect_stream
from codine.registry import ToolRegistry, ToolSpec
from codine.sessions import Session, SessionStore, validate_messages


def batch():
    return {'role': 'assistant', 'content': None, 'tool_calls': [
        {'id': name, 'type': 'function', 'function': {'name': name, 'arguments': '{}'}}
        for name in ('first', 'second', 'third')]}


def fragment(finish=None):
    function = NS(name='first', arguments='{}')
    delta = NS(content=None, reasoning_content=None, tool_calls=[NS(index=0, id='first', function=function)])
    return NS(usage=None, choices=[NS(delta=delta, finish_reason=finish)])


class FaultInjectionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.config = Config(self.root)
        self.session = Session.create(self.root, 'offline', 'system')
        self.store = SessionStore(self.root)
        self.addCleanup(self.store.close)
        self.registry = ToolRegistry()
        self.effects = []
        for name in ('first', 'second', 'third'):
            self.registry.register(ToolSpec(name, name, {'type': 'object'}, lambda name=name: self.effects.append(name) or 'done'))
        self.provider = Mock()
        self.provider.complete.return_value = (batch(), 'tool_calls', None)
        self.agent = Agent(self.config, self.provider, self.registry, self.store, self.session, lambda *args: None, Redactor())

    def test_sdk_timeout_disconnect_and_rate_limit_keep_uncertain_charge_blocked(self):
        request = httpx.Request('POST', 'https://offline.invalid/chat')
        failures = [APITimeoutError(request=request), APIConnectionError(request=request),
                    RateLimitError('rate limited', response=httpx.Response(429, request=request), body={'error': 'limit'})]
        self.config.provider = 'openrouter'
        self.config.api_key = 'offline'
        for error in failures:
            with self.subTest(error=type(error).__name__):
                self.agent.session = Session.create(self.root, 'offline', 'system', provider='openrouter')
                self.provider.reset_mock()
                self.provider.complete.side_effect = error
                with self.assertRaises(type(error)):
                    self.agent.run_turn('do work')
                self.agent.session = self.store.load(self.agent.session.id)
                self.assertEqual(self.agent.session.billing['pending'], '0.25')
                validate_messages(self.agent.session.messages)
                with self.assertRaisesRegex(BudgetError, 'unknown'):
                    self.agent.run_turn('retry')
                self.provider.complete.assert_called_once()
                self.assertEqual(self.effects, [])

    def test_partial_stream_disconnect_and_truncation_never_dispatch_tools(self):
        def disconnected():
            yield fragment()
            raise ConnectionError('socket closed after a complete-looking tool fragment')
        for stream, error in ((disconnected(), ConnectionError), (iter([fragment()]), RuntimeError),
                              (iter([fragment('length')]), RuntimeError)):
            with self.subTest(error=error, stream=stream):
                self.provider.complete.side_effect = lambda *args: collect_stream(stream)
                with self.assertRaises(error):
                    self.agent.run_turn('tool please')
                restored = self.store.load(self.session.id)
                validate_messages(restored.messages)
                self.assertIsNone(restored.pending)
                self.assertEqual(self.effects, [])

    def test_cancel_mid_batch_preserves_known_result_and_never_replays(self):
        def interrupt():
            self.effects.append('second')
            raise KeyboardInterrupt()
        self.registry.tools['second'].handler = interrupt
        with self.assertRaises(KeyboardInterrupt):
            self.agent.run_turn('batch')
        self.assertEqual(self.effects, ['first', 'second'])
        restored = self.store.load(self.session.id)
        validate_messages(restored.messages)
        results = [row for row in restored.messages if row['role'] == 'tool']
        self.assertEqual([row['tool_call_id'] for row in results], ['first', 'second', 'third'])
        self.assertEqual(results[0]['content'], 'done')
        self.assertTrue(all('outcome unknown' in row['content'] for row in results[1:]))
        self.assertFalse(restored.repair_pending())
        self.assertEqual(self.effects, ['first', 'second'])

    def test_failed_final_batch_commit_recovers_durable_results_without_replay(self):
        save = self.store.save
        def fail_commit(session):
            if session.pending is None and any(row['role'] == 'tool' for row in session.messages):
                raise OSError('disk full at batch commit')
            save(session)
        with patch.object(self.store, 'save', side_effect=fail_commit):
            with self.assertRaises(PersistenceError):
                self.agent.run_turn('batch')
        self.assertEqual(self.effects, ['first', 'second', 'third'])
        restored = self.store.load(self.session.id)
        validate_messages(restored.messages)
        self.assertEqual([row['content'] for row in restored.messages if row['role'] == 'tool'], ['done'] * 3)
        self.assertIsNone(restored.pending)
        self.assertEqual(self.effects, ['first', 'second', 'third'])

    def test_fsync_failure_preserves_prior_snapshot_and_cleans_tempfile(self):
        self.store.save(self.session)
        original = self.store.path(self.session.id).read_bytes()
        self.session.messages.append({'role': 'user', 'content': 'not durable'})
        with patch('codine.sessions.os.fsync', side_effect=OSError('disk sync failed')):
            with self.assertRaises(OSError):
                self.store.save(self.session)
        self.assertEqual(self.store.path(self.session.id).read_bytes(), original)
        self.assertFalse(list(self.store.directory.glob('.save-*')))

    def test_corrupt_pending_state_is_not_overwritten_and_lock_is_released(self):
        self.store.save(self.session)
        path = self.store.path(self.session.id)
        data = json.loads(path.read_text())
        data['pending'] = {'reply': batch(), 'results': {'unexpected-id': 'done'}}
        damaged = json.dumps(data)
        path.write_text(damaged)
        self.store.close()
        with self.assertRaises(ValueError):
            self.store.load(self.session.id)
        self.assertEqual(path.read_text(), damaged)
        # Another process/store must still be able to acquire the rejected session.
        other = SessionStore(self.root)
        self.addCleanup(other.close)
        other.acquire(self.session.id)

    def test_abrupt_process_exit_after_side_effect_recovers_without_replay(self):
        code = r'''
import os, sys
from pathlib import Path
from codine.agent import Agent
from codine.config import Config, Redactor
from codine.registry import ToolRegistry, ToolSpec
from codine.sessions import Session, SessionStore
root = Path(sys.argv[1])
session = Session.create(root, 'offline', 'system')
(root / 'session-id').write_text(session.id)
registry = ToolRegistry()
def effect(name, crash=False):
    with (root / 'effects').open('a') as handle:
        handle.write(name + '\n')
        handle.flush()
        os.fsync(handle.fileno())
    if crash:
        os._exit(73)
    return 'done'
registry.register(ToolSpec('first', '', {'type':'object'}, lambda: effect('first')))
registry.register(ToolSpec('second', '', {'type':'object'}, lambda: effect('second', True)))
class Provider:
    def complete(self, *args):
        calls = [{'id':name, 'type':'function', 'function':{'name':name, 'arguments':'{}'}} for name in ('first','second')]
        return {'role':'assistant','content':None,'tool_calls':calls}, 'tool_calls', None
Agent(Config(root), Provider(), registry, SessionStore(root), session, lambda *args: None, Redactor()).run_turn('work')
'''
        result = subprocess.run([sys.executable, '-c', code, str(self.root)], cwd=Path(__file__).resolve().parents[1],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 73, result.stderr)
        ident = (self.root / 'session-id').read_text()
        restored = self.store.load(ident)
        validate_messages(restored.messages)
        self.assertEqual(restored.messages[-2]['content'], 'done')
        self.assertIn('outcome unknown', restored.messages[-1]['content'])
        self.store.load(ident)
        self.assertEqual((self.root / 'effects').read_text(), 'first\nsecond\n')
        self.assertIsNone(restored.pending)
