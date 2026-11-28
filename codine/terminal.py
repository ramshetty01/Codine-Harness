"""A compact, scrollback-friendly terminal UI with paste-aware input and safe output."""
import json
import os
import re
import shutil
import sys
import time
from queue import Queue, Empty, Full
from prompt_toolkit import PromptSession, print_formatted_text
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.output import ColorDepth
from prompt_toolkit.styles import Style
from prompt_toolkit.utils import get_cwidth

COMMANDS = {
    '/storage': 'Inspect managed storage and review retention plans',
    '/cache': 'Reported cache counts',
    '/fallback': 'Review and confirm a fresh-context alternative',
    '/rewind-context': 'Fork conversation at a completed turn; files unchanged',
    '/fork': 'Fork this complete conversation tip', '/tree': 'Inspect branches or resume a node',
    '/packages': 'Review and install local extension packages',
    '/extensions': 'Discover and manage trusted extensions', '/ext': 'Invoke a loaded extension command',
    '/mcp': 'Review trust and manage local MCP stdio connections',
    '/trust': 'Review, allow, list or revoke exact executable extension snapshots',
    '/memory': 'Review, approve, edit or delete project facts',
    '/skills': 'Discover skill descriptions', '/skill': 'Load a scoped instruction skill',
    '/settings': 'Show redacted effective settings and their sources',
    '/status': 'Inspect phase, queue, context estimate and local cost',
    '/queue': 'Inspect queued steering and follow-ups', '/steer': 'Redirect at a safe boundary',
    '/drop': 'Remove a queued item', '/next': 'Run the next queued item', '/stop': 'Cancel the busy turn',
    '/checks': 'Discover configured tests, lint and type checks', '/git': 'Inspect Git changes and diffs',
    '/templates': 'List reusable prompt templates', '/template': 'Preview template into an unsent draft',
    '/editor': 'Open draft in configured external editor',
    '/export': 'Review and export a redacted transcript', '/import': 'Review and import historical conversation',
    '/plan': 'Inspect and plan without mutations', '/instructions': 'View project guidance',
    '/usage': 'Reported input/output/cache/reasoning usage',
    '/budget': 'View or reconcile request costs',
    '/changeset': 'Review related turn-level edits', '/rewind': 'Recover a whole file-tool changeset',
    '/changes': 'List file-tool checkpoints', '/diff': 'Review a checkpoint against the current file', '/undo': 'Review and undo a file-tool change',
    '/artifact': 'Read saved tool output', '/rename': 'Rename a session',
    '/jobs': 'Inspect background jobs', '/cancel': 'Cancel a background job',
    '/tasks': 'Inspect durable task checklist',
    '/context': 'Inspect active context and budget',
    '/policy': 'Explain user command rules',
    '/sandbox': 'Inspect command isolation',
    '/help': 'Explore commands', '/paste': 'Write a multiline task',
    '/session': 'Current session details', '/sessions': 'Browse saved sessions',
    '/resume': 'Resume a session ID', '/new': 'Start a new conversation',
    '/save': 'Save your session', '/clear': 'Clear active context; keep archive',
    '/compact': 'Summarize older context while retaining the archive',
    '/restart': 'Check code, restart, and resume', '/model': 'Show or change model',
    '/thinking': 'Show or hide reasoning', '/details': 'Page a recorded tool result', '/tools': 'Browse and expand tool groups',
    '/permissions': 'Choose ask or auto-edit', '/quit': 'Save and exit',
}
TOOL_LABELS = {
    'navigate_symbols': 'Navigate symbols',
    'list_files': 'Explore project', 'read_file': 'Read file', 'search': 'Find in project',
    'inspect_file': 'Inspect file metadata', 'move_file': 'Move file', 'rename_file': 'Rename file', 'delete_file': 'Delete file',
    'apply_changeset': 'Apply related file edits',
    'write_file': 'Create file', 'edit_file': 'Edit file', 'replace_lines': 'Replace lines', 'check_python': 'Check Python',
    'start_job': 'Start background job', 'job_status': 'Inspect job', 'cancel_job': 'Cancel job',
    'run_command': 'Run command', 'request_restart': 'Prepare restart',
}
STYLE = Style.from_dict({
    # Inherit the terminal's foreground/background so light themes work too.
    'brand': 'bold', 'border': 'ansibrightblack', 'muted': 'ansibrightblack',
    'text': '', 'accent': 'ansiyellow', 'success': 'ansigreen',
    'error': 'ansired bold', 'added': 'ansigreen', 'removed': 'ansired',
    'prompt': 'ansiyellow bold',
    'md.heading': 'bold', 'md.strong': 'bold', 'md.inline': 'ansicyan',
    'md.code': '', 'md.fence': 'ansibrightblack', 'md.list': '',
    'md.quote': 'ansibrightblack', 'md.link': 'underline', 'md.table': 'ansicyan',
    'bottom-toolbar': 'noreverse',
    'completion-menu.completion': 'noreverse',
    'completion-menu.completion.current': 'reverse bold',
    'completion-menu.meta.completion': 'ansibrightblack noreverse',
    'completion-menu.meta.completion.current': 'reverse',
})


def safe_text(text):
    # Never interpret terminal controls supplied by a file, tool, or model.
    return re.sub(r'[\x00-\x08\x0b-\x1f\x7f-\x9f]', '', str(text)).replace('\r', '')


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
    attachment_paths = None
    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if self.attachment_paths:
            from .attachments import complete_attachment
            token, names = complete_attachment(text, self.attachment_paths)
            if token is not None:
                for name in names:
                    yield Completion('@' + (json.dumps(name, ensure_ascii=False) if any(c.isspace() or c in '"\\\'@' for c in name) else name),
                                     start_position=-len(token), display=name, display_meta='Attach file snapshot')
                return
        if not text.startswith('/') or any(char.isspace() for char in text):
            return
        for command, hint in COMMANDS.items():
            if command.startswith(text.lower()):
                yield Completion(command, start_position=-len(text), display_meta=hint)


from .command_output import OutputRedactor
from .markdown_render import MarkdownStream


class Terminal:
    def __init__(self, redactor, interactive=None):
        self.redactor = redactor
        self.interactive = sys.stdin.isatty() and sys.stdout.isatty() if interactive is None else interactive
        self.color = self.interactive and 'NO_COLOR' not in os.environ and os.getenv('TERM') != 'dumb'
        self.state = lambda: {}
        self.section = None
        self.buffer = ''
        self.markdown = MarkdownStream(self._markdown_fragments)
        self.command_redactor = OutputRedactor()
        self.command_channel = None
        self.last_result = None
        self.tool_started = None
        self.tool_target = ''
        self.draft = ""
        self.busy_reader = None
        self.submitted_commands = Queue(maxsize=16)
        self.editor = None
        self.completer = CommandCompleter()
        self._editor_requested = False
        self.input_preferences = None
        self.prompt_history = None
        if self.interactive:
            self._configure_editor()

    def _configure_editor(self, settings=None, history=None):
        from .input_settings import DEFAULT_KEYS
        from prompt_toolkit.filters import is_searching
        from prompt_toolkit.key_binding.bindings.search import start_reverse_incremental_search
        keys = settings['keys'] if settings else DEFAULT_KEYS
        bindings = KeyBindings()
        @bindings.add(*keys['submit'], filter=~is_searching)
        def submit(event):
            event.current_buffer.validate_and_handle()
        @bindings.add(*keys['newline'], filter=~is_searching)
        def newline(event):
            event.current_buffer.insert_text('\n')
        bindings.add(*keys['history_search'], filter=~is_searching)(start_reverse_incremental_search)
        @bindings.add(*keys['editor'], filter=~is_searching)
        def edit_draft(event):
            self.draft = event.current_buffer.text
            self._editor_requested = True
            event.app.exit(result='/editor')
        self.editor = PromptSession(multiline=True, key_bindings=bindings, history=history,
            prompt_continuation=FormattedText([('class:border', '  · ')]),
            bottom_toolbar=self.toolbar, style=STYLE if self.color else Style.from_dict({}),
            color_depth=None if self.color else ColorDepth.DEPTH_1_BIT,
            completer=self.completer, complete_while_typing=True,
            reserve_space_for_menu=4, enable_history_search=False, refresh_interval=.25)

    def configure_input(self, project):
        if not self.interactive:
            return
        from pathlib import Path
        from .input_settings import preferences, PrivateHistory, INPUT_PATH, HISTORY_PATH
        if any(path.resolve().is_relative_to(Path(project).resolve()) for path in (INPUT_PATH, HISTORY_PATH)):
            self.input_preferences = self.prompt_history = None
            self._configure_editor()
            self.write('User input settings and persistent history disabled: their paths are inside this project. Default keys remain available; choose a narrower project to enable user customization.')
            return
        settings = preferences(project)
        from prompt_toolkit.history import DummyHistory
        history = PrivateHistory(HISTORY_PATH, lambda: self.redactor) if settings['history'] else DummyHistory()
        self.input_preferences = settings
        self.prompt_history = history if settings['history'] else None
        self._configure_editor(settings, history)

    def external_editor(self):
        if not self.interactive or self.busy_reader is not None:
            raise ValueError('External editor requires an idle interactive terminal.')
        from .input_settings import edit_text
        settings = self.input_preferences or {}
        self._editor_requested = False
        self.draft = edit_text(self.draft, settings.get('editor_argv'), redactor=self.redactor,
                               terminal_fd=sys.stdin.fileno())
        self.write('Editor text returned as an unsent draft. Review it, then submit when ready.')

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
        self.line('  / commands   ·   /editor draft   ·   Ctrl+D exit', 'muted')
        if self.input_preferences:
            keys = self.input_preferences['keys']
            self.line('  Keys: ' + ' · '.join(action + '=' + ' '.join(sequence) for action, sequence in keys.items()), 'muted')

    def toolbar(self):
        from .status_view import lines
        state = self.state()
        if not isinstance(state, dict):
            return self.clean(state)
        # Policy remains first; separate rows keep costs/context from being
        # confused with cumulative usage. /status exposes unclipped details.
        text = '\n'.join(clip(self.clean(row), self.width) for row in lines(state, compact=True))
        return FormattedText([('class:muted', text)])

    def submit_command(self, text):
        if len(text) > 16000:
            raise ValueError('Terminal command exceeds 16,000 characters.')
        try:
            self.submitted_commands.put_nowait(text)
        except Full:
            raise ValueError('Terminal command queue is full (16); wait for the prompt.') from None

    def has_submitted_commands(self):
        return not self.submitted_commands.empty()

    def prompt(self):
        try:
            submitted = self.submitted_commands.get_nowait()
        except Empty:
            pass
        else:
            self.line('› ' + submitted)
            return submitted
        if not self.editor:
            raise EOFError()
        if self.prompt_history and self.prompt_history.failure:
            self.write(self.prompt_history.failure)
        self.line()
        try:
            text = self.editor.prompt(FormattedText([('class:prompt', '› ')]), default=self.draft)
        except KeyboardInterrupt:
            self.draft = self.editor.default_buffer.text
            raise
        if not self._editor_requested:
            self.draft = ''
        if self.prompt_history and self.prompt_history.failure:
            self.write(self.prompt_history.failure)
        return text

    def paste(self):
        self.panel('Paste a task', ['Paste your task. Put /end on its own line to send.', 'Ctrl+C cancels this input.'], 'accent')
        lines = []
        try:
            while True:
                line = input('  · ')
                if line == '/end':
                    return '\n'.join(lines)
                lines.append(line)
        except KeyboardInterrupt:
            self.draft = '\n'.join(lines)
            raise

    def approve(self, description):
        self._approval_pending = True
        try:
            if self.busy_reader:
                with self.busy_reader.approval():
                    return self._approve(description)
            return self._approve(description)
        finally:
            self._approval_pending = False

    def _approve(self, description):
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
        except KeyboardInterrupt:
            self.line('  Interrupted. This approval was not granted.', 'accent')
            raise
        except EOFError:
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
            if value.get('id') and status in ('queued', 'running', 'cancelled'):
                title += ' · job ' + str(value['id'])
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
            self.line('    … /details to page output · /tools for earlier groups', 'muted')

    def _markdown_fragments(self, fragments):
        print_formatted_text(FormattedText([(f'class:{tone}', value) for tone, value in fragments]),
                             style=STYLE, end='', flush=True)

    def _flush(self, final=False):
        # Hold only a possible secret prefix at the chunk boundary. Holding the
        # longest key's length unconditionally hides unrelated short responses.
        keep = 0
        if not final:
            for secret in self.redactor.secrets:
                for size in range(min(len(self.buffer), len(secret) - 1), keep, -1):
                    if self.buffer.endswith(secret[:size]):
                        keep = size
                        break
        cut = len(self.buffer) if final else max(0, len(self.buffer) - keep)
        for secret in self.redactor.secrets:
            start = self.buffer.find(secret)
            while start >= 0:
                if start < cut < start + len(secret):
                    cut = start
                start = self.buffer.find(secret, start + 1)
        if cut:
            clean = self.clean(self.buffer[:cut])
            if self.color and self.section == 'text':
                self.markdown.feed(clean)
                self.markdown.flush_partial()
            else:
                print(clean, end='', flush=True)
            self.buffer = self.buffer[cut:]
        if final:
            self.markdown.finish()

    def emit(self, kind, text):
        if kind == 'command_output':
            event = self.command_redactor.feed(text, self.redactor)
            if event['text']:
                if self.section != 'command_output' or self.command_channel != event['stream']:
                    self.emit('end_stream', '')
                    self.heading('Command output · ' + event['stream'], 'muted')
                    self.section = 'command_output'
                    self.command_channel = event['stream']
                print(self.clean(event['text']), end='', flush=True)
            return
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
                detail = text.get('reason', text['outcome']).replace('_', ' ')
                self.line('  Run stopped: ' + detail, 'accent')
            elif text.get('task_state') in ('blocked', 'unresolved', 'stale_plan'):
                self.line('  Task plan: ' + text['task_state'].replace('_', ' '), 'muted')
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
