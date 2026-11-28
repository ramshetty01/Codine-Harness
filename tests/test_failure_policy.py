import io
import json
import urllib.error
import unittest
from email.utils import formatdate
from unittest.mock import Mock, patch

import httpx2
from openai import (APIConnectionError, APITimeoutError, AuthenticationError,
                    BadRequestError, InternalServerError, PermissionDeniedError, RateLimitError)

from codine.failure_policy import classify_failure, retry_after, retry_metadata_read
from codine.router_catalog import metadata

REQUEST = httpx2.Request('POST', 'https://provider.invalid/chat/completions')


def sdk_error(cls, status, headers=None, code=None):
    return cls('fake-secret-in-provider-message',
               response=httpx2.Response(status, request=REQUEST, headers=headers or {}),
               body={'code': code, 'message': 'fake-secret-in-body'})


class FakeClock:
    def __init__(self):
        self.now = 0
        self.sleeps = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class FailurePolicyTests(unittest.TestCase):
    def test_sdk_errors_are_classified_without_claiming_completion_replay_safe(self):
        cases = [
            (sdk_error(AuthenticationError, 401), 'authentication', False),
            (sdk_error(PermissionDeniedError, 403), 'permission', False),
            (sdk_error(RateLimitError, 429), 'rate_limit', True),
            (sdk_error(RateLimitError, 429, code='insufficient_quota'), 'quota', False),
            (sdk_error(InternalServerError, 503), 'provider_unavailable', True),
            (sdk_error(BadRequestError, 400, code='context_length_exceeded'), 'context_limit', False),
            (APITimeoutError(REQUEST), 'timeout', True),
            (APIConnectionError(request=REQUEST), 'connection', True),
        ]
        for error, category, transient in cases:
            with self.subTest(category=category):
                result = classify_failure(error)
                self.assertEqual(result.category, category)
                self.assertEqual(result.transient, transient)
                self.assertFalse(result.retry_safe)
                self.assertIn('No automatic completion retry', result.action)
                self.assertNotIn('fake-secret', json.dumps(result.as_dict()))

    def test_retry_after_numeric_dates_and_invalid_values(self):
        for value, expected in [('2', 2), (formatdate(1004, usegmt=True), 4), ('-1', None),
                                ('NaN', None), ('Infinity', None), ('bad', None), ('9999999', 86400)]:
            error = sdk_error(RateLimitError, 429, {'Retry-After': value})
            self.assertEqual(retry_after(error, now=1000), expected)

    def test_metadata_retries_are_bounded_with_injected_time(self):
        clock = FakeClock()
        read = Mock(side_effect=[urllib.error.URLError('hidden'),
                                sdk_error(InternalServerError, 503), {'models': []}])
        result = retry_metadata_read(read, clock=clock.clock, sleep=clock.sleep, jitter=lambda: 1)
        self.assertEqual(result, {'models': []})
        self.assertEqual(read.call_count, 3)
        self.assertEqual(clock.sleeps, [0.5, 1])
        self.assertTrue(all(call.args[0] <= 5 for call in read.call_args_list))

    def test_long_server_hint_does_not_trigger_early_or_unbounded_retry(self):
        clock = FakeClock()
        read = Mock(side_effect=sdk_error(RateLimitError, 429, {'Retry-After': '300'}))
        with self.assertRaises(RateLimitError):
            retry_metadata_read(read, clock=clock.clock, sleep=clock.sleep)
        read.assert_called_once()
        self.assertEqual(clock.sleeps, [])

    def test_authorization_schema_errors_and_cancellation_never_retry(self):
        for error in (sdk_error(AuthenticationError, 401), ValueError('invalid data'), KeyboardInterrupt()):
            read = Mock(side_effect=error); clock = FakeClock()
            with self.assertRaises(type(error)):
                retry_metadata_read(read, clock=clock.clock, sleep=clock.sleep)
            read.assert_called_once()
            self.assertFalse(clock.sleeps)

    def test_elapsed_budget_stops_scheduling_retries(self):
        clock = FakeClock()
        def read(timeout):
            clock.now += 20
            raise TimeoutError('request took entire budget')
        with self.assertRaises(TimeoutError):
            retry_metadata_read(read, clock=clock.clock, sleep=clock.sleep, total_timeout=20)
        self.assertFalse(clock.sleeps)

    def test_metadata_get_has_real_retry_path_and_does_not_disclose_key(self):
        clock = FakeClock()
        error = urllib.error.HTTPError('https://openrouter.ai/api/v1/models', 503,
                                       'fake-secret', {}, io.BytesIO(b'private-error-body'))
        successful = io.BytesIO(b'{"data": [{"id":"offline-model"}]}')
        def retry(read):
            return retry_metadata_read(read, clock=clock.clock, sleep=clock.sleep, jitter=lambda: 1)
        with patch('codine.router_catalog.retry_metadata_read', side_effect=retry), \
             patch('urllib.request.urlopen', side_effect=[error, successful]) as opened:
            self.assertEqual(metadata('models', 'fake-api-key'), [{'id': 'offline-model'}])
        self.assertEqual(opened.call_count, 2)
        self.assertTrue(error.fp.closed)
        self.assertEqual(opened.call_args.args[0].get_method(), 'GET')
        self.assertTrue(all(call.kwargs['timeout'] <= 5 for call in opened.call_args_list))

    def test_malformed_metadata_is_not_retried(self):
        with patch('urllib.request.urlopen', return_value=io.BytesIO(b'not-json')) as opened:
            with self.assertRaisesRegex(ValueError, 'no inference requested'):
                metadata('models')
        opened.assert_called_once()

    def test_metadata_oversize_and_missing_data_fail_without_retry(self):
        for body in (b'x' * 8_000_001, b'{"unexpected": []}'):
            with self.subTest(size=len(body)), patch('urllib.request.urlopen', return_value=io.BytesIO(body)) as opened:
                with self.assertRaisesRegex(ValueError, 'metadata unavailable'):
                    metadata('models')
            opened.assert_called_once()

    def test_metadata_attempt_cap_stops_persistent_transient_failures(self):
        clock = FakeClock()
        read = Mock(side_effect=urllib.error.URLError('hidden'))
        with self.assertRaises(urllib.error.URLError):
            retry_metadata_read(read, clock=clock.clock, sleep=clock.sleep, jitter=lambda: 1)
        self.assertEqual(read.call_count, 3)
        self.assertEqual(clock.sleeps, [0.5, 1])

    def test_sdk_completion_auto_retries_stay_disabled(self):
        from pathlib import Path
        from codine.config import Config
        from codine.provider import NvidiaProvider, OpenRouterProvider
        config = Config(Path.cwd(), api_key='fake-only', max_tokens=512)
        for adapter in (NvidiaProvider, OpenRouterProvider):
            error = sdk_error(RateLimitError, 429)
            client = Mock(); client.chat.completions.create.side_effect = error
            with patch('codine.provider.OpenAI', return_value=client) as constructor:
                with self.assertRaises(RateLimitError):
                    adapter(config).complete([], [], 'fake/model', lambda *args: None)
            self.assertEqual(constructor.call_args.kwargs['max_retries'], 0)
            client.chat.completions.create.assert_called_once()

    def test_unknown_completion_charge_blocks_replay_for_real_sdk_failures(self):
        import tempfile
        from pathlib import Path
        from codine.agent import Agent, SYSTEM
        from codine.budget import Budget, BudgetError
        from codine.config import Config, Redactor
        from codine.sessions import Session, SessionStore
        for error in (sdk_error(RateLimitError, 429), APITimeoutError(REQUEST), APIConnectionError(request=REQUEST)):
            with self.subTest(error=type(error).__name__), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                session = Session.create(root, 'offline', SYSTEM, provider='openrouter')
                ledger = Budget.create(); session.billing = ledger.state
                provider = Mock(); provider.complete.side_effect = error
                store = SessionStore(root)
                try:
                    agent = Agent(Config(root, provider='openrouter', api_key='fake-only'), provider,
                                  None, store, session, Mock(), Redactor())
                    with self.assertRaises(type(error)):
                        agent.complete([], [], ledger)
                    restored = store.load(session.id)
                    self.assertEqual(restored.billing['pending'], '0.25')
                    with self.assertRaisesRegex(BudgetError, 'unknown'):
                        agent.complete([], [], ledger)
                    provider.complete.assert_called_once()
                finally:
                    store.close()

    def test_cli_advice_is_sanitized_and_requires_charge_reconciliation(self):
        from codine.cli import show_error
        ui = Mock()
        show_error(ui, sdk_error(RateLimitError, 429, {'Retry-After': '2'}))
        text = ui.write.call_args.args[0]
        self.assertIn('/budget reconcile', text)
        self.assertIn('retry-after hint: 2s', text)
        self.assertNotIn('fake-secret', text)
