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

    def test_nvidia_model_command_stays_local(self):
        import tempfile
        from codine.cli import main as cli_main
        with tempfile.TemporaryDirectory() as project, patch('codine.cli.NvidiaProvider') as provider, redirect_stdout(io.StringIO()) as output:
            self.assertEqual(cli_main(['--project', project, '--prompt', '/model']), 0)
        provider.return_value.complete.assert_not_called()
        self.assertIn('NVIDIA:', output.getvalue())


if __name__ == '__main__':
    unittest.main()
