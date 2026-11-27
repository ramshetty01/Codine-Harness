"""Offline SDK/SSE fixtures, not live provider compatibility evidence."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

import httpx2
from openai import OpenAI, APIError
from openai.types.chat import ChatCompletionChunk

from codine.provider import OpenRouterProvider, collect_openrouter_stream
from codine.config import Config, Redactor
from codine.agent import Agent, SYSTEM
from codine.budget import Budget, BudgetError
from codine.sessions import Session, SessionStore


def frame(delta=None, finish=None, usage=None, empty=False):
    data = dict(id='generation-test', object='chat.completion.chunk', created=1, model='fake',
                choices=[] if empty else [dict(index=0, delta=delta or {}, finish_reason=finish)])
    if usage is not None:
        data['usage'] = usage
    return data


def chunk(**kwargs):
    return ChatCompletionChunk(**frame(**kwargs))


def call(index=0, ident='call_a', name='read_file', arguments='{}'):
    return dict(index=index, id=ident, type='function', function=dict(name=name, arguments=arguments))


class Body(httpx2.SyncByteStream):
    def __init__(self, data):
        self.data, self.closed = data, False
    def __iter__(self):
        # Split within SSE delimiters/JSON/UTF-8 to exercise the SDK decoder.
        for i in range(0, len(self.data), 7):
            yield self.data[i:i+7]
    def close(self):
        self.closed = True


class RouterStreamTests(unittest.TestCase):
    def provider(self, frames):
        data = b': OPENROUTER PROCESSING\n\n' + b''.join(
            ('data: '+json.dumps(f, ensure_ascii=False)+'\n\n').encode() for f in frames) + b'data: [DONE]\n\n'
        body = Body(data)
        requests = []
        def handle(request):
            requests.append(json.loads(request.content))
            return httpx2.Response(200, headers={'content-type':'text/event-stream'}, stream=body)
        provider = OpenRouterProvider(Config(Path.cwd(), api_key='fake-key'))
        provider.client = OpenAI(api_key='fake-key', base_url='https://fixture.invalid/v1',
            max_retries=0, http_client=httpx2.Client(transport=httpx2.MockTransport(handle)))
        self.addCleanup(provider.close)
        return provider, body, requests

    def test_real_sdk_sse_interleaved_tools_and_usage_only(self):
        frames = [frame({'role':'assistant','content':'héllo'}),
            frame({'tool_calls':[call(1,'b','read_', '{"path":')]}),
            frame({'tool_calls':[call(0,'a','list_', '{}'), call(1,None,'file','"x.py"}')]}),
            frame({'tool_calls':[call(0,None,'files','')]}, 'tool_calls'),
            frame(empty=True, usage={'prompt_tokens':3,'completion_tokens':4,'total_tokens':7,'cost':0.001})]
        provider, body, requests = self.provider(frames)
        events = []
        reply, finish, usage = provider.complete([], [], 'fake', lambda *e:events.append(e))
        self.assertEqual(finish, 'tool_calls')
        self.assertEqual([c['id'] for c in reply['tool_calls']], ['a','b'])
        self.assertEqual(reply['tool_calls'][1]['function'], {'name':'read_file','arguments':'{"path":"x.py"}'})
        self.assertEqual(usage.cost, .001)
        self.assertEqual(events, [('text','héllo')])
        self.assertTrue(body.closed)
        self.assertEqual(len(requests), 1)
        self.assertTrue(requests[0]['stream'])

    def test_reasoning_sequence_signatures_and_opaque_payload(self):
        details = [{'type':'reasoning.text','text':'first','index':0},
            {'type':'reasoning.text','text':' second','index':0,'signature':'sig'},
            {'type':'reasoning.text','text':'next','index':0},
            {'type':'reasoning.encrypted','data':'opaque','custom':{'a':[1]}},
            {'type':'reasoning.encrypted','data':'opaque','custom':{'a':[1]}},
            {'type':'reasoning.summary','summary':'a'}, {'type':'reasoning.summary','summary':'b'}]
        original = copy.deepcopy(details)
        events = []
        reply, _, _ = collect_openrouter_stream([
            chunk(delta={'reasoning_details':[d]}) for d in details] + [chunk(finish='length')], lambda *e:events.append(e), True)
        self.assertIsNone(reply['content'])
        self.assertEqual(reply['reasoning_details'][0]['text'], 'first second')
        self.assertEqual(reply['reasoning_details'][0]['signature'], 'sig')
        self.assertEqual(reply['reasoning_details'][1]['text'], 'next')
        self.assertEqual(reply['reasoning_details'][2:4], details[3:5])
        self.assertEqual(reply['reasoning_details'][4]['summary'], 'ab')
        self.assertEqual(details, original)
        self.assertNotIn('opaque', str(events))

    def test_normalized_reasoning_not_double_emitted_and_final_usage_snapshot(self):
        events = []
        reply, _, usage = collect_openrouter_stream([
            chunk(delta={'reasoning':'think','reasoning_details':[{'type':'reasoning.text','text':'think'}]},
                  usage={'prompt_tokens':1,'completion_tokens':1,'total_tokens':2,'cost':99}),
            chunk(finish='length'), chunk(delta={'role':'assistant','content':''}, finish='length',
                  usage={'prompt_tokens':1,'completion_tokens':3,'total_tokens':4,'cost':.002})], lambda *e:events.append(e), True)
        self.assertEqual(events, [('reasoning','think')])
        self.assertEqual(reply['reasoning'], 'think')
        self.assertEqual(usage.cost, .002)
        _, _, usage = collect_openrouter_stream([chunk(usage={'prompt_tokens':1,'completion_tokens':1,'total_tokens':2,'cost':99}),chunk(finish='stop')])
        self.assertIsNone(usage)

    def test_malformed_and_incomplete_fail_closed(self):
        cases = [[], [frame({'content':'partial'})], [frame({'tool_calls':[call()]},'length')],
                 [frame(finish='tool_calls')], [frame(finish='stop'),frame({'content':'late'})],
                 [frame(finish='stop'),frame(finish='length')],
                 [frame({'tool_calls':[call(1)]},'tool_calls')],
                 [frame({'tool_calls':[call(0),call(1)]},'tool_calls')],
                 [frame({'tool_calls':[call()]}),frame({'tool_calls':[call(0,'other')]},'tool_calls')],
                 [frame({'role':'user'},'stop')], [frame({'content':'x'*2_000_001},'stop')],
                 [{'error':{'message':'private provider detail'},'choices':[]}]]
        for arguments in ('{', '[]', '{"x":NaN}'):
            cases.append([frame({'tool_calls':[call(arguments=arguments)]},'tool_calls')])
        for frames in cases:
            with self.subTest(frames=str(frames)[:100]), self.assertRaises(RuntimeError):
                collect_openrouter_stream(frames)

    def test_sdk_error_and_cancel_close_without_retry(self):
        provider, body, requests = self.provider([{'error':{'message':'failure','code':500}}])
        with self.assertRaises(APIError): provider.complete([], [], 'fake', None)
        self.assertTrue(body.closed); self.assertEqual(len(requests),1)
        provider, body, requests = self.provider([frame({'content':'partial'}),frame(finish='stop')])
        def cancel(*args): raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt): provider.complete([], [], 'fake', cancel)
        self.assertTrue(body.closed); self.assertEqual(len(requests),1)

    def test_reasoning_roundtrip_through_sdk_request_and_hidden_display(self):
        opaque = {'type':'reasoning.encrypted','data':'unchanged-blob','id':'opaque-1','format':'provider-v1','index':0}
        provider, body, requests = self.provider([frame({'reasoning_details':[opaque],'content':'ok'}, 'stop'),
            frame(empty=True, usage={'prompt_tokens':1,'completion_tokens':1,'total_tokens':2,'cost':.001})])
        events=[]
        reply, _, _ = provider.complete([], [], 'fake', lambda *e:events.append(e))
        self.assertEqual(events, [('text','ok')])
        provider.complete([reply, {'role':'user','content':'continue'}], [], 'fake', None)
        self.assertEqual(requests[1]['messages'][0]['reasoning_details'], [opaque])

    def test_unknown_final_cost_keeps_shared_reservation(self):
        provider, body, requests = self.provider([frame({'tool_calls':[call()]},'tool_calls'),
            frame(empty=True, usage={'prompt_tokens':1,'completion_tokens':1,'total_tokens':2})])
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); store=SessionStore(root); self.addCleanup(store.close)
            session=Session.create(root,'fake',SYSTEM,provider='openrouter')
            budget=Budget.create(); session.billing=budget.state
            registry=Mock(); registry.schemas.return_value=[]
            agent=Agent(Config(root,provider='openrouter',api_key='fake-key'),provider,registry,store,session,Mock(),Redactor())
            ledger=root/'budget.json'
            agent.configure_shared_budget(ledger,Mock(return_value={'limit_remaining':5}))
            with self.assertRaises(BudgetError): agent.complete([],[],budget)
            self.assertIsNotNone(budget.state['pending'])
            self.assertIsNotNone(json.loads(ledger.read_text())['pending'])
            registry.execute.assert_not_called()
            self.assertEqual(len(requests),1)


if __name__ == '__main__': unittest.main()
