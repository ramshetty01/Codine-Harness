import json
import ssl
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import httpx2 as httpx
from openai import APIConnectionError, APIStatusError, DefaultHttpxClient
from codine.provider import NvidiaProvider, OpenRouterProvider
from codine.connection_retry import create_stream


def event(content=None, finish=None, usage=None):
    value = {'id': 'offline', 'object': 'chat.completion.chunk', 'created': 0, 'model': 'offline',
             'choices': [{'index': 0, 'delta': {'content': content}, 'finish_reason': finish}]}
    if usage is not None: value['usage'] = usage
    return ('data: ' + json.dumps(value) + '\n\n').encode()


def response():
    return httpx.Response(200, headers={'content-type': 'text/event-stream'}, content=
        event('ok') + event(finish='stop', usage={'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2, 'cost': 0.001}) + b'data: [DONE]\n\n')


class ConnectionRetryTests(unittest.TestCase):
    def provider(self, kind, handler):
        factory = lambda **kwargs: DefaultHttpxClient(transport=httpx.MockTransport(handler), **kwargs)
        self.addCleanup(patch.stopall)
        patch('codine.provider.DefaultHttpxClient', side_effect=factory).start()
        provider = kind(SimpleNamespace(api_key='offline-fake-key', show_reasoning=False))
        self.addCleanup(provider.close)
        return provider

    def test_both_owned_providers_retry_setup_and_return_one_completion(self):
        for kind in (NvidiaProvider, OpenRouterProvider):
            with self.subTest(provider=kind.__name__):
                calls, delays, events = [], [], []
                def handler(request):
                    calls.append(request)
                    if len(calls) < 3:
                        raise httpx.ConnectError('fake setup failure', request=request)
                    return response()
                provider = self.provider(kind, handler)
                with patch('codine.connection_retry.time.sleep', side_effect=delays.append):
                    reply, finish, usage = provider._send_prepared('offline', [{'role': 'user', 'content': 'hello'}], {'stream': True}, lambda *row: events.append(row))
                self.assertEqual(len(calls), 3)
                self.assertEqual(delays, [.25, .5])
                self.assertEqual(reply['content'], 'ok')
                self.assertEqual(finish, 'stop')
                self.assertEqual([row for row in events if row[0] == 'text'], [('text', 'ok')])
                self.assertEqual(provider.client.max_retries, 0)
                self.assertFalse(provider.client._client.follow_redirects)
                self.assertEqual(calls[0].extensions['timeout']['connect'], 5)
                self.assertEqual(calls[0].content, calls[2].content)
                provider.close()
                patch.stopall()

    def test_exhausted_connect_timeouts_are_bounded(self):
        calls = []
        def handler(request):
            calls.append(request)
            raise httpx.ConnectTimeout('fake timeout', request=request)
        provider = self.provider(NvidiaProvider, handler)
        with patch('codine.connection_retry.time.sleep'), self.assertRaises(APIConnectionError):
            provider._send_prepared('offline', [], {'stream': True}, lambda *_: None)
        self.assertEqual(len(calls), 3)

    def test_sent_http_errors_and_redirects_never_retry(self):
        for status in (307, 401, 429, 503):
            with self.subTest(status=status):
                calls = []
                def handler(request):
                    calls.append(request)
                    return httpx.Response(status, headers={'location': 'https://other.invalid/endpoint'}, json={'error': {'message': 'fake'}})
                provider = self.provider(NvidiaProvider, handler)
                with self.assertRaises(APIStatusError):
                    provider._send_prepared('offline', [], {'stream': True}, lambda *_: None)
                self.assertEqual(len(calls), 1)
                provider.close(); patch.stopall()

    def test_write_read_and_certificate_failures_never_retry(self):
        for kind in (httpx.WriteError, httpx.ReadTimeout, ssl.SSLCertVerificationError):
            with self.subTest(error=kind.__name__):
                calls = []
                def handler(request):
                    calls.append(request)
                    if kind is ssl.SSLCertVerificationError:
                        try: raise kind('fake certificate problem')
                        except ssl.SSLCertVerificationError as error:
                            raise httpx.ConnectError('fake TLS setup', request=request) from error
                    raise kind('fake transmission failure', request=request)
                provider = self.provider(NvidiaProvider, handler)
                with self.assertRaises(APIConnectionError):
                    provider._send_prepared('offline', [], {'stream': True}, lambda *_: None)
                self.assertEqual(len(calls), 1)
                provider.close(); patch.stopall()

    def test_partial_stream_is_not_replayed(self):
        calls, shown = [], []
        class Broken(httpx.SyncByteStream):
            def __iter__(self):
                yield event('partial')
                raise httpx.ReadError('fake dropped stream')
        def handler(request):
            calls.append(request)
            return httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=Broken())
        provider = self.provider(NvidiaProvider, handler)
        with self.assertRaises(APIConnectionError):
            provider._send_prepared('offline', [], {'stream': True}, lambda *row: shown.append(row))
        self.assertEqual(len(calls), 1)
        self.assertIn(('text', 'partial'), shown)

    def test_shared_billing_settles_once_and_exhaustion_still_blocks(self):
        from codine.agent import Agent, SYSTEM
        from codine.budget import Budget, BudgetError
        from codine.config import Config, Redactor
        from codine.sessions import Session, SessionStore
        for succeeds in (True, False):
            with self.subTest(succeeds=succeeds), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                calls = []
                def handler(request):
                    calls.append(request)
                    if not succeeds or len(calls) < 3:
                        raise httpx.ConnectError('fake setup failure', request=request)
                    return response()
                provider = self.provider(OpenRouterProvider, handler)
                config = Config(root, model='offline', provider='openrouter', api_key='offline-fake-key')
                provider.config = config
                provider.capability_record = lambda _: None
                provider.capability_declarations = lambda _: {}
                store = SessionStore(root)
                self.addCleanup(store.close)
                session = Session.create(root, 'offline', SYSTEM, provider='openrouter')
                ledger = Budget.create()
                session.billing = ledger.state
                agent = Agent(config, provider, None, store, session, lambda *_: None, Redactor())
                shared = root / 'shared' / 'budget.json'
                agent.configure_shared_budget(shared, lambda *_: {'limit_remaining': 5})
                with patch('codine.connection_retry.time.sleep'):
                    if succeeds:
                        agent.complete([{'role': 'user', 'content': 'hello'}], [], ledger)
                        self.assertEqual(session.usage_details['requests'], 1)
                        self.assertEqual(session.billing['spent'], '0.001')
                    else:
                        with self.assertRaises(APIConnectionError):
                            agent.complete([{'role': 'user', 'content': 'hello'}], [], ledger)
                        self.assertIsNotNone(session.billing['pending'])
                        with self.assertRaises(BudgetError):
                            agent.complete([{'role': 'user', 'content': 'retry'}], [], ledger)
                self.assertEqual(len(calls), 3)
                state = json.loads(shared.read_text())
                self.assertEqual(len(state['records']), 1)
                self.assertEqual(state['spent'], '0.001' if succeeds else '0')
                provider.close(); store.close(); patch.stopall()

    def test_stop_prevents_next_attempt_and_injected_client_has_no_retries(self):
        calls = []
        def handler(request):
            calls.append(request)
            raise httpx.ConnectError('fake setup', request=request)
        provider = self.provider(NvidiaProvider, handler)
        def stop(*_): raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            provider._send_prepared('offline', [], {'stream': True}, stop)
        self.assertEqual(len(calls), 1)
        with self.assertRaises(APIConnectionError):
            create_stream(provider.client, {'model': 'offline', 'messages': [], 'stream': True}, lambda *_: None)
        self.assertEqual(len(calls), 2)
