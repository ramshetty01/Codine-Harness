import io
import json
import os
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch
from prompt_toolkit.document import Document
from prompt_toolkit.utils import get_cwidth
from codine.config import Redactor
from codine.terminal import CommandCompleter, Terminal


class TerminalUITests(unittest.TestCase):
    def test_completions_are_only_for_slash_commands(self):
        completer = CommandCompleter()
        matches = list(completer.get_completions(Document('/de'), None))
        self.assertEqual([item.text for item in matches], ['/details'])
        self.assertEqual(list(completer.get_completions(Document('please /de'), None)), [])
        self.assertEqual(list(completer.get_completions(Document('/model test'), None)), [])

    def test_failure_is_visible_and_secrets_stay_redacted(self):
        terminal = Terminal(Redactor(['private-key-value']), interactive=False)
        out = io.StringIO()
        with redirect_stdout(out):
            terminal.emit('tool', 'run_command')
            terminal.emit('result', json.dumps({'status': 'completed', 'exit_code': 2, 'stderr': 'private-key-value failed'}))
            terminal.details()
        self.assertIn('Needs attention', out.getvalue())
        self.assertIn('exit 2', out.getvalue())
        self.assertNotIn('private-key-value', out.getvalue())
        self.assertNotIn('\x1b', out.getvalue())

    def test_short_preview_does_not_discard_full_result(self):
        terminal = Terminal(Redactor(), interactive=False)
        output = json.dumps({'path': 'notes.md', 'content': '\n'.join(['line'] * 20 + ['last marker'])})
        preview, details = io.StringIO(), io.StringIO()
        with redirect_stdout(preview):
            terminal.emit('result', output)
        with redirect_stdout(details):
            terminal.details()
        self.assertNotIn('last marker', preview.getvalue())
        self.assertIn('last marker', details.getvalue())

    def test_narrow_panel_wraps_without_losing_text(self):
        terminal = Terminal(Redactor(), interactive=False)
        output = io.StringIO()
        with patch('codine.terminal.shutil.get_terminal_size', return_value=os.terminal_size((38, 24))), redirect_stdout(output):
            terminal.panel('REVIEW', ['路' * 24])
        lines = output.getvalue().splitlines()
        self.assertTrue(all(get_cwidth(line) == 36 for line in lines))
        self.assertEqual(output.getvalue().count('路'), 24)


if __name__ == '__main__':
    unittest.main()
