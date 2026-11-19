import copy
import io
import os
import unittest
from contextlib import redirect_stdout
from unittest.mock import MagicMock, patch

from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

import main
from model_picker import build_model_dialog, free_tool_models, select_model


def record(model_id='test/free', **changes):
    result = {
        'id': model_id,
        'name': model_id,
        'pricing': {'prompt': '0', 'completion': '0'},
        'architecture': {'input_modalities': ['text'], 'output_modalities': ['text']},
        'supported_parameters': ['tools'],
        'context_length': 32000,
    }
    result.update(changes)
    return result


class ModelPickerTests(unittest.TestCase):
    def test_filters_prices_and_capabilities(self):
        records = [
            record(),
            record('paid', pricing={'prompt': '0', 'completion': '0.0001'}),
            record('unknown', pricing={}),
            record('bad-price', pricing={'prompt': 'NaN', 'completion': '0'}),
            record('request-fee', pricing={'prompt': '0', 'completion': '0', 'request': '1'}),
            record('chat-only', supported_parameters=[]),
            record('image', architecture={'input_modalities': ['text'], 'output_modalities': ['image']}),
            record(),
        ]
        self.assertEqual([model['id'] for model in free_tool_models(records)], ['test/free'])

    def test_keyboard_selection(self):
        with create_pipe_input() as keyboard:
            with create_app_session(input=keyboard, output=DummyOutput()):
                app = build_model_dialog([record('a'), record('b')], 'a')
                keyboard.send_text('\x1b[B \t\r')  # Down, Space, Tab, Enter
                self.assertEqual(app.run(), 'b')

    def test_escape_cancels(self):
        with create_pipe_input() as keyboard:
            with create_app_session(input=keyboard, output=DummyOutput()):
                app = build_model_dialog([record('a')], 'a')
                keyboard.send_text('\x1b')
                self.assertIsNone(app.run())

    def test_numbered_fallback_reprompts(self):
        with patch('model_picker.sys.stdin.isatty', return_value=False), patch('builtins.input', side_effect=['99', '2']), redirect_stdout(io.StringIO()):
            self.assertEqual(select_model([record('a'), record('b')], 'a'), 'b')

    def test_switch_changes_next_request_and_preserves_chat(self):
        turns = []

        def turn(client, messages, model, max_tokens, parameters):
            turns.append((model, copy.deepcopy(messages), parameters))
            messages.append({'role': 'assistant', 'content': 'Hello', 'reasoning_details': [{'text': 'provider-specific'}]})

        with patch.object(main, 'load_dotenv'), patch.dict(os.environ, {'OPENROUTER_API_KEY': 'test', 'OPENROUTER_MODEL': 'a'}), patch.object(main, 'OpenAI', return_value=MagicMock()), patch.object(main, 'fetch_free_models', return_value=[record('b')]), patch.object(main, 'select_model', return_value='b'), patch.object(main, 'run_turn', side_effect=turn), patch('builtins.input', side_effect=['Hello', '/model', 'Again', '/quit']), redirect_stdout(io.StringIO()):
            main.main()
        self.assertEqual([turn[0] for turn in turns], ['a', 'b'])
        self.assertEqual(turns[1][1][2]['content'], 'Hello')
        self.assertNotIn('reasoning_details', turns[1][1][2])
        self.assertEqual(turns[1][2], ['tools'])

    def test_empty_catalog_does_not_open_picker(self):
        with patch.object(main, 'load_dotenv'), patch.dict(os.environ, {'OPENROUTER_API_KEY': 'test'}), patch.object(main, 'OpenAI', return_value=MagicMock()), patch.object(main, 'fetch_free_models', return_value=[]), patch.object(main, 'select_model') as picker, patch('builtins.input', side_effect=['/model', 'exit']), redirect_stdout(io.StringIO()) as output:
            main.main()
        picker.assert_not_called()
        self.assertIn('Current model unchanged', output.getvalue())


if __name__ == '__main__':
    unittest.main()
