"""A compact, scrollback-friendly terminal UI with paste-aware input and safe output."""
import json
import os
import re
import shutil
import sys
import time
from prompt_toolkit import PromptSession, print_formatted_text
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.output import ColorDepth
from prompt_toolkit.styles import Style
from prompt_toolkit.utils import get_cwidth

COMMANDS = {
    '/plan': 'Inspect and plan without mutations', '/instructions': 'View project guidance',
    '/budget': 'View or reconcile request costs',
    '/changes': 'List file-tool checkpoints', '/diff': 'Review a checkpoint against the current file', '/undo': 'Review and undo a file-tool change',
    '/help': 'Explore commands', '/paste': 'Write a multiline task',
    '/session': 'Current session details', '/sessions': 'Browse saved sessions',
    '/resume': 'Resume a session ID', '/new': 'Start a new conversation',
    '/save': 'Save your session', '/clear': 'Clear active context; keep archive',
    '/compact': 'Keep the latest three turns in context',
    '/restart': 'Check code, restart, and resume', '/model': 'Show or change model',
    '/thinking': 'Show or hide reasoning', '/details': 'Full output of the last tool',
    '/permissions': 'Choose ask or auto-edit', '/quit': 'Save and exit',
}
TOOL_LABELS = {
    'list_files': 'Explore project', 'read_file': 'Read file', 'search': 'Find in project',
    'write_file': 'Create file', 'edit_file': 'Edit file', 'replace_lines': 'Replace lines', 'check_python': 'Check Python',
    'run_command': 'Run command', 'request_restart': 'Prepare restart',
}
STYLE = Style.from_dict({
    # Inherit the terminal's foreground/background so light themes work too.
    'brand': 'bold', 'border': 'ansibrightblack', 'muted': 'ansibrightblack',
    'text': '', 'accent': 'ansiyellow', 'success': 'ansigreen',
    'error': 'ansired bold', 'added': 'ansigreen', 'removed': 'ansired',
    'prompt': 'ansiyellow bold',
    'bottom-toolbar': 'noreverse',
    'completion-menu.completion': 'noreverse',
    'completion-menu.completion.current': 'reverse bold',
    'completion-menu.meta.completion': 'ansibrightblack noreverse',
    'completion-menu.meta.completion.current': 'reverse',
})


def safe_text(text):
    # Never interpret terminal controls supplied by a file, tool, or model.
    return re.sub(r'[\x00-\x08\x0b-\x1f\x7f]', '', str(text)).replace('\r', '')


def clip(text, width):
    text = str(text)
    if get_cwidth(text) <= width:
        return text
    result = ''
    for char in text:
        if get_cwidth(result + char) > max(0, width - 1):
            break
        result += char
    return result + '…'


class CommandCompleter(Completer):
    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if not text.startswith('/') or any(char.isspace() for char in text):
            return
        for command, hint in COMMANDS.items():
            if command.startswith(text.lower()):
                yield Completion(command, start_position=-len(text), display_meta=hint)


class Terminal:
    def __init__(self, redactor, interactive=None):
        self.redactor = redactor
        self.interactive = sys.stdin.isatty() and sys.stdout.isatty() if interactive is None else interactive
        self.color = self.interactive and 'NO_COLOR' not in os.environ and os.getenv('TERM') != 'dumb'
        self.state = lambda: {}
        self.section = None
        self.buffer = ''
        self.last_result = None
        self.tool_started = None
        self.tool_target = ''
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
                prompt_continuation=FormattedText([('class:border', '  · ')]),
                bottom_toolbar=self.toolbar, style=STYLE if self.color else Style.from_dict({}),
                color_depth=None if self.color else ColorDepth.DEPTH_1_BIT,
                completer=CommandCompleter(), complete_while_typing=True,
                reserve_space_for_menu=4, enable_history_search=False)

    @property
    def width(self):
        return max(20, min(88, shutil.get_terminal_size((80, 24)).columns - 2))

    def clean(self, text):
        return safe_text(self.redactor.text(str(text)))

    def line(self, text='', tone='text'):
        text = self.clean(text)
        if self.color:
            print_formatted_text(FormattedText([(f'class:{tone}', text)]), style=STYLE)
        else:
            print(text, flush=True)

    def write(self, text=''):
        text = self.clean(text)
        # Diffs keep every line visible before approval, with familiar +/- colors.
        if text.startswith('--- a/'):
            for line in text.splitlines():
                tone = 'muted' if line.startswith(('---', '+++', '@@')) else 'added' if line.startswith('+') else 'removed' if line.startswith('-') else 'text'
                self.line(line, tone)
        else:
            self.line(text, 'error' if text.startswith('Error:') else 'text')

    def heading(self, label, tone='brand'):
        self.line('  ' + self.clean(label), tone)

    def panel(self, title, rows, tone='brand'):
        """An open margin; wrap every detail without truncating approvals."""
        self.heading(title, tone)
        available = self.width - 4
        for row in rows:
            rest = self.clean(row).expandtabs(4)
            if not rest:
                self.line('  │', 'border')
            while rest:
                line = ''
                for char in rest:
                    if get_cwidth(line + char) > available:
                        break
                    line += char
                rest = rest[len(line):]
                self.line('  │ ' + line)

    def welcome(self, project, session, model):
        self.line()
        self.heading('[/] codine')
        self.line('    ' + str(project), 'muted')
        self.line('    ' + model, 'muted')
        self.line()
        self.line('  / commands   ·   Alt+Enter newline   ·   Ctrl+D exit', 'muted')

    def toolbar(self):
        state = self.state()
        if not isinstance(state, dict):
            return self.clean(state)
        mode = {'ask': 'ask before edits', 'plan': 'PLAN · read only', 'auto-edit': 'auto-edit'}.get(state.get('mode'), 'ask')
        # Mode is first so narrow terminals never hide the execution policy.
        text = f'  {mode}  ·  {state.get("tokens", 0):,} tokens'
        model = state.get('model', '')
        if model:
            text += '  ·  ' + model
        return FormattedText([('class:muted', clip(self.clean(text), self.width))])

    def prompt(self):
        if not self.editor:
            raise EOFError()
        self.line()
        return self.editor.prompt(FormattedText([('class:prompt', '› ')]))

    def paste(self):
        self.panel('Paste a task', ['Paste your task. Put /end on its own line to send.', 'Ctrl+C cancels this input.'], 'accent')
        lines = []
        while True:
            line = input('  · ')
            if line == '/end':
                return '\n'.join(lines)
            lines.append(line)

    def approve(self, description):
        self.emit('end_stream', '')
        self.line()
        self.panel('Review required', str(description).splitlines() + ['', 'yes = approve    anything else = decline'], 'accent')
        if not self.interactive:
            self.line('  Declined: approval requires an interactive terminal.', 'accent')
            return False
        try:
            answer = PromptSession(style=STYLE if self.color else Style.from_dict({}), color_depth=None if self.color else ColorDepth.DEPTH_1_BIT).prompt('Approve? Type yes: ')
            approved = answer.strip().lower() == 'yes'
            self.line('  Approved' if approved else '  Declined', 'success' if approved else 'accent')
            return approved
        except (KeyboardInterrupt, EOFError):
            self.line('  Declined.', 'accent')
            return False

    def details(self):
        if self.last_result is None:
            self.line('No tool output yet. Ask Codine to inspect your project first.', 'muted')
            return
        self.heading('Tool output')
        self.write(self.last_result)

    def _result(self, text):
        self.last_result = self.clean(text)
        elapsed = f' · {time.monotonic() - self.tool_started:.1f}s' if self.tool_started else ''
        self.tool_started = None
        try:
            value = json.loads(self.last_result)
        except (ValueError, TypeError):
            value = None
        lines = []
        title, tone, symbol = 'Completed', 'muted', '└'
        if isinstance(value, dict):
            status = value.get('status')
            if value.get('error') or value.get('ok') is False or status in ('timeout', 'output_limit') or value.get('exit_code') not in (None, 0):
                title, tone, symbol = 'Needs attention', 'error', '!'
            elif status in ('declined', 'skipped'):
                title, tone, symbol = status.capitalize(), 'accent', '└'
            elif status:
                title = status.replace('_', ' ').capitalize()
            elif value.get('ok'):
                title = 'Syntax valid'
            if 'path' in value:
                title += ' · ' + str(value['path'])
            if 'files' in value and isinstance(value['files'], list):
                title = f'{len(value["files"])} files found'
                lines = [str(item) for item in value['files']]
            elif 'matches' in value and isinstance(value['matches'], list):
                title = f'{len(value["matches"])} matches found'
                lines = [f'{item.get("path")}:{item.get("line")}  {item.get("text")}' for item in value['matches'] if isinstance(item, dict)]
            elif 'content' in value:
                lines = str(value['content']).splitlines()
            if 'stdout' in value or 'stderr' in value:
                title += f' · exit {value.get("exit_code")}'
                if value.get('stdout'):
                    lines += ['stdout:'] + str(value['stdout']).splitlines()
                if value.get('stderr'):
                    lines += ['stderr:'] + str(value['stderr']).splitlines()
            for key in ('error', 'message', 'reason'):
                if value.get(key):
                    lines += str(value[key]).splitlines()
            if value.get('next_page'):
                lines.append('More file content is available on the next page.')
            if value.get('truncated') or value.get('output_truncated'):
                lines.append('Tool output was limited; narrow the request for more.')
        else:
            lines = self.last_result.splitlines()
        self.line(f'  {symbol} {title}{elapsed}', tone)
        limit = 8 if tone == 'error' else 3
        for line in lines[:limit]:
            self.line('    ' + clip(line, self.width - 4), 'muted')
        if len(lines) > limit or any(get_cwidth(line) > self.width - 4 for line in lines[:limit]):
            self.line('    … /details for full output', 'muted')

    def _flush(self, final=False):
        keep = max(map(len, self.redactor.secrets), default=1) - 1
        cut = len(self.buffer) if final else max(0, len(self.buffer) - keep)
        for secret in self.redactor.secrets:
            start = self.buffer.find(secret)
            while start >= 0:
                if start < cut < start + len(secret):
                    cut = start
                start = self.buffer.find(secret, start + 1)
        if cut:
            print(self.clean(self.buffer[:cut]), end='', flush=True)
            self.buffer = self.buffer[cut:]

    def emit(self, kind, text):
        if kind in ('text', 'reasoning'):
            if self.section != kind:
                self.emit('end_stream', '')
                self.line()
                self.heading('codine' if kind == 'text' else 'thinking', 'brand' if kind == 'text' else 'muted')
                self.section = kind
            self.buffer += text
            self._flush()
            return
        self._flush(final=True)
        if self.section:
            print(flush=True)
        self.section = None
        if kind == 'tool_request':
            # Use only a small action label, never dump edit bodies or full arguments.
            try:
                args = json.loads(text['function']['arguments'])
                target = args.get('path', args.get('query', '')) if isinstance(args, dict) else ''
                self.tool_target = self.clean(target).replace('\n', ' ').replace('\t', ' ')
            except (KeyError, TypeError, ValueError):
                self.tool_target = ''
            return
        if kind in ('end_stream', 'run_start', 'tool_response'):
            return
        if kind == 'run_end':
            if text.get('outcome') not in ('completed', 'command'):
                self.line('  Run stopped: ' + text['outcome'], 'accent')
            return
        if kind == 'tool':
            self.tool_started = time.monotonic()
            label = TOOL_LABELS.get(text, text)
            target = '  ' + self.tool_target if self.tool_target else ''
            self.line(clip(f'  · {label}{target}', self.width))
            self.tool_target = ''
        elif kind == 'result':
            self._result(text)
        else:
            self.line(f'  · {text}', 'muted')
