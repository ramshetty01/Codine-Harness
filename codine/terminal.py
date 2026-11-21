"""Paste-aware terminal input and a small, dependency-light event renderer."""
import re
import sys
from prompt_toolkit import PromptSession
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style


def safe_text(text):
    # Model output is data; do not let ANSI escapes alter the terminal.
    return re.sub(r'[\x00-\x08\x0b-\x1f\x7f]', '', str(text)).replace('\r', '')


class Terminal:
    def __init__(self, redactor, interactive=None):
        self.redactor = redactor
        self.interactive = sys.stdin.isatty() and sys.stdout.isatty() if interactive is None else interactive
        self.state = lambda: 'Codine'
        self.section = None
        self.buffer = ''
        self.editor = None
        if self.interactive:
            bindings = KeyBindings()
            @bindings.add('enter')
            def submit(event):
                event.current_buffer.validate_and_handle()
            @bindings.add('escape', 'enter')
            def newline(event):
                event.current_buffer.insert_text('\n')
            self.editor = PromptSession(multiline=True, key_bindings=bindings,
                prompt_continuation='  · ', bottom_toolbar=lambda: safe_text(self.state()),
                style=Style.from_dict({'bottom-toolbar': 'bg:#253444 #d5e5ef'}),
                enable_history_search=True)

    def write(self, text=''):
        print(safe_text(self.redactor.text(str(text))), flush=True)

    def prompt(self):
        if not self.editor:
            raise EOFError()
        return self.editor.prompt('\nYou › ')

    def paste(self):
        self.write('Paste text; put /end on a line by itself to send. Ctrl+C cancels.')
        lines = []
        while True:
            line = input('… ')
            if line == '/end':
                return '\n'.join(lines)
            lines.append(line)

    def approve(self, description):
        self.emit('end_stream', '')
        self.write(description)
        if not self.interactive:
            self.write('[Declined: approval requires an interactive terminal.]')
            return False
        try:
            # No persistent history and no reuse of the model prompt buffer.
            answer = PromptSession().prompt('Approve? Type yes: ')
            return answer.strip().lower() == 'yes'
        except (KeyboardInterrupt, EOFError):
            self.write('Declined.')
            return False

    def _flush(self, final=False):
        # Hold enough trailing characters to redact a secret split across chunks.
        keep = max(map(len, self.redactor.secrets), default=1) - 1
        cut = len(self.buffer) if final else max(0, len(self.buffer) - keep)
        for secret in self.redactor.secrets:
            start = self.buffer.find(secret)
            while start >= 0:
                if start < cut < start + len(secret):
                    cut = start
                start = self.buffer.find(secret, start + 1)
        if cut:
            print(safe_text(self.redactor.text(self.buffer[:cut])), end='', flush=True)
            self.buffer = self.buffer[cut:]

    def emit(self, kind, text):
        if kind in ('text', 'reasoning'):
            if self.section != kind:
                self.emit('end_stream', '')
                self.write('\nCodine:' if kind == 'text' else '\nThinking:')
                self.section = kind
            self.buffer += text
            self._flush()
            return
        self._flush(final=True)
        if self.section:
            print(flush=True)
        self.section = None
        if kind == 'end_stream':
            return
        if kind == 'tool':
            self.write(f'  → {text}')
        elif kind == 'result':
            self.write('    ' + text[:1600] + (' … [display shortened; full result saved]' if len(text) > 1600 else ''))
        else:
            self.write(f'[{text}]')
