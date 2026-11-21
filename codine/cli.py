"""CLI composition and slash commands. The model never handles slash commands."""
import argparse
from pathlib import Path
import sys

from openai import APIConnectionError, APIError, APIStatusError, AuthenticationError, RateLimitError
from .agent import Agent, PersistenceError, SYSTEM
from .config import APP_ROOT, Config, Redactor
from .context import compact
from .provider import NvidiaProvider
from .restart import check_sources, restart_process
from .sessions import Session, SessionStore
from .terminal import Terminal
from .tools import build_registry
from .workspace import Workspace

HELP = '''Commands
  /help                 Show this guide
  /paste                Read multiple lines until /end (fallback for paste)
  /session, /sessions   Current session / saved session IDs
  /resume ID, /new      Resume saved history / start a new session
  /save                 Save now (also automatic after every boundary)
  /clear                Omit old history from new requests; keep archive
  /compact              Keep the latest 3 whole turns in active context
  /restart              Save, check, restart, and resume with updated code
  /model [ID]           Show/change NVIDIA model; compatibility varies
  /thinking             Toggle display of reasoning (generation unchanged)
  /permissions MODE     ask or auto-edit; commands/core edits still ask
  exit, /quit           Save and quit
Enter sends. Alt+Enter adds a line. Bracketed paste stays one prompt.
Tools: list_files, read_file, search, write_file, edit_file, check_python,
       run_command, request_restart.
Commands run as your user, not in a sandbox. Inspect every command approval.
'''


def parser():
    result = argparse.ArgumentParser(description='Codine — a Pi-inspired Python terminal coding harness')
    result.add_argument('--project', default='.', help='Workspace directory (default: current directory)')
    group = result.add_mutually_exclusive_group()
    group.add_argument('--resume', metavar='ID', help='Resume a project session')
    group.add_argument('--continue', dest='continue_session', action='store_true', help='Resume most recently saved session')
    result.add_argument('--prompt', help='Run one prompt; stdin pipes are also accepted')
    result.add_argument('--check', action='store_true', help='Validate startup without credentials or session writes')
    return result


def show_error(ui, error):
    ui.emit('end_stream', '')
    if isinstance(error, AuthenticationError):
        message = 'NVIDIA rejected the API key (401). Check NVIDIA_API_KEY; shell variables override .env.'
    elif isinstance(error, RateLimitError):
        message = 'NVIDIA rate limit reached. Wait before trying again. Completed tool results remain saved.'
    elif isinstance(error, APIConnectionError):
        message = 'NVIDIA connection failed or timed out. History and completed tools remain saved.'
    elif isinstance(error, APIStatusError):
        message = f'NVIDIA returned HTTP {error.status_code}. Check model access, request compatibility, and quota.'
    elif isinstance(error, APIError):
        message = 'NVIDIA request failed. History remains saved.'
    else:
        message = str(error)
    ui.write('Error: ' + message)


def command(text, agent, workspace, ui):
    name, _, argument = text.partition(' ')
    argument = argument.strip()
    name = name.lower()
    session = agent.session
    if name == '/help':
        ui.write(HELP)
    elif name == '/session':
        ui.write(f'Session: {session.id}\nProject: {session.project}\nModel: {session.model}\nMessages: {len(session.messages)} | active boundary: {session.context_start}\nFile: {agent.store.path(session.id)}')
    elif name == '/sessions':
        rows = agent.store.list()
        ui.write('\n'.join(f'{row["id"]}  {row["messages"]} messages  {row["model"]}' for row in rows) or 'No sessions.')
    elif name == '/save':
        agent.save()
        ui.write('Saved ' + session.id)
    elif name in ('/resume', '/new'):
        agent.save()
        if name == '/resume':
            if not argument:
                raise ValueError('Usage: /resume SESSION_ID')
            replacement = agent.store.load(argument)
        else:
            replacement = Session.create(workspace.root, session.model, SYSTEM)
            agent.store.save(replacement)
        agent.session = replacement
        workspace.read_versions.clear()
        workspace.read_ranges.clear()
        agent.restart_requested = False
        ui.write('Active session: ' + replacement.id)
    elif name == '/clear':
        session.context_start = len(session.messages)
        agent.save()
        ui.write('Active context cleared. The full session archive remains on disk.')
    elif name == '/compact':
        changed = compact(session)
        agent.save()
        ui.write('Active context keeps the latest 3 complete turns; archive retained.' if changed else 'Already at 3 turns or fewer.')
    elif name == '/model':
        if argument:
            if any(char.isspace() for char in argument) or len(argument) > 200:
                raise ValueError('Provide one model ID.')
            session.model = argument
            agent.save()
        ui.write(f'NVIDIA: {session.model}\nModel must support this provider\'s chat, tools, and thinking parameters; there is no automatic fallback.')
    elif name == '/thinking':
        agent.config.show_reasoning = not agent.config.show_reasoning
        ui.write('Reasoning display: ' + ('on' if agent.config.show_reasoning else 'off'))
    elif name == '/permissions':
        if argument not in ('ask', 'auto-edit'):
            raise ValueError('Usage: /permissions ask|auto-edit')
        if argument == 'auto-edit' and not ui.approve('Enable automatic ordinary file edits for this process? Core edits and commands still require approval.'):
            return
        workspace.mode = argument
        ui.write('Permissions: ' + argument)
    elif name == '/restart':
        restart_process(agent)
    else:
        raise ValueError('Unknown command. Use /help.')


def main(argv=None):
    args = parser().parse_args(argv)
    if args.check:
        count = check_sources()
        registry = build_registry(Workspace(APP_ROOT, lambda _: False), lambda: None)
        print(f'Startup check passed: {count} Python files, {len(registry.schemas())} tools. No API request or session write.')
        return 0
    store = provider = None
    ui = Terminal(Redactor())
    try:
        config = Config.load(args.project)
        redactor = Redactor.environment()
        ui.redactor = redactor
        store = SessionStore(config.project, redactor)
        resume = args.resume
        if args.continue_session:
            rows = store.list()
            resume = rows[0]['id'] if rows else None
        session = store.load(resume) if resume else Session.create(config.project, config.model, SYSTEM)
        store.save(session)
        provider = NvidiaProvider(config)
        workspace = Workspace(config.project, ui.approve, ui.write, APP_ROOT)
        agent = Agent(config, provider, None, store, session, ui.emit, redactor)
        agent.registry = build_registry(workspace, lambda: agent.request_restart(ui.approve))
        ui.state = lambda: f'{agent.session.model} | {workspace.root.name} | {agent.session.id[-8:]} | {workspace.mode} | {agent.session.usage["total"]} tokens | Alt+Enter newline'
        ui.write(f'Codine · NVIDIA · {session.model}\nProject: {config.project}\nSession: {session.id}\n/help for commands · Alt+Enter for newline · exit to quit')
        if not config.api_key:
            ui.write('No NVIDIA_API_KEY configured. Offline commands are available; model requests need a key.')
        one_shot = args.prompt
        if one_shot is None and not sys.stdin.isatty():
            one_shot = sys.stdin.read()
        def turn(text):
            try:
                agent.run_turn(text)
                if agent.restart_requested:
                    agent.restart_requested = False
                    restart_process(agent)
                return True
            except PersistenceError:
                raise
            except KeyboardInterrupt:
                agent.recover_interrupt()
                ui.emit('end_stream', '')
                ui.write('Turn interrupted. Completed results saved; unknown outcomes recorded. You can continue.')
            except (APIError, OSError, ValueError, RuntimeError) as error:
                # Unexpected tool failure may leave a pending batch. Finalize without replay.
                agent.recover_interrupt()
                show_error(ui, error)
            return False
        if one_shot is not None:
            if not one_shot.strip():
                return 0
            if one_shot.strip().startswith('/'):
                command(one_shot.strip(), agent, workspace, ui)
                return 0
            return 0 if turn(one_shot) else 1
        while True:
            try:
                text = ui.prompt().strip()
                if not text:
                    continue
                if text.lower() in ('exit', '/quit'):
                    break
                if text == '/paste':
                    text = ui.paste()
                    if text.strip():
                        turn(text)
                elif text.startswith('/'):
                    command(text, agent, workspace, ui)
                else:
                    turn(text)
            except PersistenceError:
                raise
            except KeyboardInterrupt:
                ui.write('Input cancelled. Use exit or Ctrl+D to quit.')
            except EOFError:
                break
            except (OSError, ValueError, RuntimeError) as error:
                show_error(ui, error)
        agent.save()
        ui.write('Saved. Goodbye!')
        return 0
    except (OSError, ValueError, PersistenceError) as error:
        show_error(ui, error)
        return 1
    finally:
        if store:
            store.close()
        if provider:
            provider.close()


if __name__ == '__main__':
    sys.exit(main())
