import unittest
from unittest.mock import Mock, patch
from prompt_toolkit import PromptSession
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from codine.config import Redactor
from codine.terminal import Terminal


class InputCancellationTests(unittest.TestCase):
    def terminal(self):
        ui = Terminal(Redactor())
        ui.line = Mock()
        ui.panel = Mock()
        return ui

    def test_real_editor_preserves_unsent_draft_after_interrupt(self):
        ui = self.terminal()
        with create_pipe_input() as pipe:
            ui.editor = PromptSession(input=pipe, output=DummyOutput())
            pipe.send_text('unfinished task\x03')
            with self.assertRaises(KeyboardInterrupt):
                ui.prompt()
            self.assertEqual(ui.draft, 'unfinished task')
            pipe.send_text(' continued\r')
            self.assertEqual(ui.prompt(), 'unfinished task continued')
            self.assertEqual(ui.draft, '')

    def test_approval_interrupt_propagates_and_paste_retains_completed_lines(self):
        ui = self.terminal()
        ui.interactive = True
        with patch('codine.terminal.PromptSession') as editor:
            editor.return_value.prompt.side_effect = KeyboardInterrupt
            with self.assertRaises(KeyboardInterrupt):
                ui.approve('Write file?')
        with patch('builtins.input', side_effect=['first line', 'second line', KeyboardInterrupt]):
            with self.assertRaises(KeyboardInterrupt):
                ui.paste()
        self.assertEqual(ui.draft, 'first line\nsecond line')
