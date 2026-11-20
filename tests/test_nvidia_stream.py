import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import MagicMock, patch
from openai.types.chat import ChatCompletionChunk
import main


def chunk(delta=None, finish=None, usage=None):
    return ChatCompletionChunk(id='test', created=0, model=main.DEFAULT_MODEL, object='chat.completion.chunk', choices=[] if delta is None and usage else [{'index':0, 'delta':delta or {}, 'finish_reason':finish}], usage=usage)


class NVIDIAStreamTests(unittest.TestCase):
    def collect(self, chunks):
        with redirect_stdout(io.StringIO()):
            return main.collect_stream(chunks)

    def test_content_reasoning_and_usage(self):
        reply, finish, usage = self.collect([
            chunk({'reasoning_content':'Thinking'}),
            chunk({'content':'Hello '}), chunk({'content':'there'}, 'stop'),
            chunk(usage={'prompt_tokens':3,'completion_tokens':4,'total_tokens':7}),
        ])
        self.assertEqual(reply['content'], 'Hello there')
        self.assertEqual(reply['reasoning_content'], 'Thinking')
        self.assertEqual(usage.total_tokens, 7)
        self.assertEqual(finish, 'stop')

    def test_interleaved_tool_fragments(self):
        reply, _, _ = self.collect([
            chunk({'tool_calls':[{'index':0,'id':'call0','type':'function','function':{'name':'read_project_', 'arguments':'{"filename":'}}]}),
            chunk({'tool_calls':[{'index':1,'id':'call1','type':'function','function':{'name':'list_project_files', 'arguments':'{}'}}]}),
            chunk({'tool_calls':[{'index':0,'function':{'name':'file', 'arguments':'"main.py"}'}}]}, 'tool_calls'),
        ])
        self.assertEqual(reply['tool_calls'][0]['function']['name'], 'read_project_file')
        self.assertEqual(reply['tool_calls'][0]['function']['arguments'], '{"filename":"main.py"}')
        self.assertEqual(reply['tool_calls'][1]['id'], 'call1')

    def test_truncated_tools_rejected(self):
        with self.assertRaises(RuntimeError):
            self.collect([chunk({'tool_calls':[{'index':0,'id':'call0','function':{'name':'edit_project_file','arguments':'{'}}]}, 'length')])

    def test_unfinished_stream_rejected(self):
        with self.assertRaises(RuntimeError):
            self.collect([chunk({'content':'partial'})])

    def test_tool_result_round_trip(self):
        streams = [MagicMock(), MagicMock()]
        streams[0].__enter__.return_value = iter([chunk({'tool_calls':[{'index':0,'id':'call0','type':'function','function':{'name':'list_project_files','arguments':'{}'}}]}, 'tool_calls')])
        streams[1].__enter__.return_value = iter([chunk({'content':'Done'}, 'stop')])
        client = MagicMock()
        client.chat.completions.create.side_effect = streams
        messages = [{'role':'user','content':'List files'}]
        with patch.object(main, 'execute_tool', return_value='main.py'), redirect_stdout(io.StringIO()):
            main.run_turn(client, messages, main.DEFAULT_MODEL, 512)
        self.assertEqual(messages[2], {'role':'tool','tool_call_id':'call0','content':'main.py'})
        self.assertEqual(messages[3]['content'], 'Done')
        request = client.chat.completions.create.call_args.kwargs
        self.assertTrue(request['stream'])
        self.assertTrue(request['parallel_tool_calls'])
        self.assertEqual(request['extra_body'], {'chat_template_kwargs':{'enable_thinking':True}})


if __name__ == '__main__':
    unittest.main()
