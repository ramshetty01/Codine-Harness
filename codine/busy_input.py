"""One busy input reader, suspended and joined before any approval reader starts."""
from contextlib import contextmanager
import threading
import time
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.patch_stdout import patch_stdout


class InputOwnershipError(RuntimeError):
    pass


class PauseInput(Exception):
    pass


def queue_command(inbox, text, write, *, busy=False):
    command, _, argument = text.strip().partition(' ')
    if command == '/queue':
        items = inbox.list()
        write('Queued input: ' + str(len(items)) + '/32')
        for item in items:
            write(f"  {item['id'][:8]}  {item['lane']}  {item['text'][:120]}")
    elif command == '/drop':
        inbox.remove(argument.strip())
        write('Removed queued input.')
    elif command == '/steer':
        item = inbox.add(argument, 'steer')
        write('Queued steering ' + item['id'][:8] + ' · applies at the next safe boundary.')
    elif command == '/stop' and busy:
        inbox.cancel.set()
        write('Cancellation requested · finishing or cancelling the underway action safely; follow-ups stay queued.')
    elif busy and not command.startswith('/'):
        item = inbox.add(text, 'followup')
        write('Queued follow-up ' + item['id'][:8] + ' · runs after this turn completes.')
    else:
        return False
    return True


class BusyInput:
    def __init__(self, ui, inbox):
        self.ui, self.inbox = ui, inbox
        self.stop = threading.Event()
        self.thread = None
        self.draft = ''
        self.failure = None

    def start(self):
        self.stop.clear()
        self.thread = threading.Thread(target=self.read, name='codine-input', daemon=True)
        self.thread.start()

    def read(self):
        while not self.stop.is_set():
            try:
                text = self.ui.editor.prompt(FormattedText([('class:prompt', 'busy › ')]), default=self.draft)
                self.draft = ''
                if text.strip().lower() == 'exit':
                    self.ui.submit_command(text)
                    self.ui.write('Command queued for prompt · runs after this turn stops.')
                elif text.strip() and not queue_command(self.inbox, text, self.ui.write, busy=True):
                    # Main thread dispatches commands only after this reader joins.
                    self.ui.submit_command(text)
                    self.ui.write('Command queued for prompt · runs after this turn stops.')
            except PauseInput:
                self.draft = self.ui.editor.default_buffer.text
                return
            except KeyboardInterrupt:
                self.draft = self.ui.editor.default_buffer.text
                self.inbox.cancel.set()
                self.ui.write('Cancellation requested. Unsent draft preserved; follow-ups remain queued.')
                return
            except EOFError:
                self.inbox.cancel.set()
                return
            except ValueError as error:
                self.ui.write(str(error))
            except BaseException as error:
                self.failure = error
                self.inbox.failure = error
                self.inbox.cancel.set()
                return

    def pause(self):
        self.stop.set()
        deadline = time.monotonic() + 5
        while self.thread and self.thread.is_alive():
            app = self.ui.editor.app
            loop = app.loop
            if loop and app.is_running:
                def exit_prompt():
                    if app.is_running and not app.is_done:
                        app.exit(exception=PauseInput())
                try:
                    loop.call_soon_threadsafe(exit_prompt)
                except RuntimeError:
                    pass  # Prompt event loop closed while its thread was finishing.
            self.thread.join(timeout=0.02)
            if time.monotonic() >= deadline:
                raise InputOwnershipError('Busy input reader did not stop; refusing to open a competing approval prompt.')

    @contextmanager
    def approval(self):
        self.pause()
        try:
            self.inbox.check_cancel()
            yield
        finally:
            if not self.inbox.cancel.is_set() and not self.inbox.failure:
                self.start()


@contextmanager
def busy_input(ui, inbox):
    if not ui.interactive or getattr(ui, 'rpc_transport', False):
        yield
        return
    reader = BusyInput(ui, inbox)
    ui.busy_reader = reader
    ui.write('Working · text queues follow-up · /steer TEXT redirects · /stop cancels')
    with patch_stdout(raw=True):
        reader.start()
        try:
            yield
        finally:
            reader.pause()
            ui.busy_reader = None
            if reader.draft:
                ui.draft = reader.draft
    if reader.failure:
        raise RuntimeError('Busy input failed; pending input remains saved.') from reader.failure
