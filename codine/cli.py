"""CLI composition and slash commands. The model never handles slash commands."""
import argparse
from pathlib import Path
import sys

from openai import APIConnectionError, APIError, APIStatusError, AuthenticationError, RateLimitError
from .agent import Agent, PersistenceError, SYSTEM
from .config import APP_ROOT, Config, Redactor
from . import __version__
from .context import compact
from .context_inspector import inspect_context, format_context
from .provider import NvidiaProvider, OpenRouterProvider
from .budget import Budget
from .events import JsonTerminal
from .instructions import load_instructions
from .restart import check_sources, restart_process
from .sessions import Session, SessionStore
from .terminal import Terminal
from .tools import build_registry, execution_status
from .workspace import Workspace

HELP = '''Commands
  /help                 Show this guide
  /plan                 Enter read-only planning; /permissions ask exits
  /sandbox              Inspect effective command isolation and image
  /policy               Show user command rules and last explained decision
  /instructions         Show loaded root project guidance
  /budget               Show session spending; reconcile AMOUNT if unknown
  /paste                Read multiple lines until /end (fallback for paste)
  /session, /sessions   Current session / saved session IDs
  /resume ID, /new      Resume saved history / start a new session
  /save                 Save now (also automatic after every boundary)
  /clear                Omit old history from new requests; keep archive
  /context              Inspect active context and estimated remaining space
  /compact              Keep the latest 3 whole turns in active context
  /restart              Save, check, restart, and resume with updated code
  /model [ID|free]       Pick an OpenRouter model, or set a model ID
  /details              Full output of the last tool
  /changes              List recent file-tool checkpoints
  /diff [ID]            Compare checkpoint with the current file
  /undo [ID]            Preview and restore one file-tool change
  /thinking             Toggle display of reasoning (generation unchanged)
  /permissions MODE     ask or auto-edit; commands use /policy, core edits ask
  exit, /quit           Save and quit
Enter sends. Alt+Enter adds a line. Bracketed paste stays one prompt.
Tools: list_files, read_file, search, write_file, edit_file, replace_lines, check_python,
       run_command, request_restart.
Use /sandbox to inspect command isolation. File tools operate in the host workspace.
'''


def parser():
    result = argparse.ArgumentParser(prog='codine', description='Codine — a Pi-inspired Python terminal coding harness')
    result.add_argument('--version', action='version', version=f'%(prog)s {__version__}')
    result.add_argument('--project', default='.', help='Workspace directory (default: current directory)')
    group = result.add_mutually_exclusive_group()
    group.add_argument('--resume', metavar='ID', help='Resume a project session')
    group.add_argument('--continue', dest='continue_session', action='store_true', help='Resume most recently saved session')
    result.add_argument('--prompt', help='Run one prompt; stdin pipes are also accepted')
    result.add_argument('--provider', choices=('nvidia', 'openrouter'))
    result.add_argument('--model', help='Explicit model ID for the selected provider')
    result.add_argument('--executor', choices=('host', 'docker'), help='Command executor; resume retains saved choice, new sessions default host')
    result.add_argument('--policy', help='Explicit user-owned command policy JSON; project files are never auto-loaded')
    result.add_argument('--image', help='Locally installed Docker image; resolved to an immutable image ID')
    result.add_argument('--plan', action='store_true', help='Read-only tools; no commands, edits, or model-requested restart')
    result.add_argument('--json', action='store_true', help='One-shot JSONL event output; approvals denied')
    result.add_argument('--check', action='store_true', help='Validate startup without credentials or session writes')
    return result


def show_error(ui, error):
    ui.emit('end_stream', '')
    if isinstance(error, AuthenticationError):
        message = 'Provider rejected the API key (401). Check the selected provider API key; shell variables override .env.'
    elif isinstance(error, RateLimitError):
        message = 'Provider rate limit reached. Wait before trying again. Completed tool results remain saved.'
    elif isinstance(error, APIConnectionError):
        message = 'Provider connection failed or timed out. History and completed tools remain saved.'
    elif isinstance(error, APIStatusError):
        message = f'Provider returned HTTP {error.status_code}. Check model access, request compatibility, and quota.'
    elif isinstance(error, APIError):
        message = 'Provider request failed. History remains saved.'
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
    elif name == '/policy':
        policy = getattr(workspace, 'command_policy', None)
        ui.write(policy.describe() if policy else 'No command rules; commands require approval.')
        decision = getattr(workspace, 'last_policy_decision', None)
        if decision:
            ui.write('Last decision: ' + decision.reason)
    elif name == '/sandbox':
        ui.write(execution_status(workspace))
    elif name == '/plan':
        workspace.mode = 'plan'
        ui.write('Planning mode: read/search/syntax tools only. Use /permissions ask to enable changes.')
    elif name == '/instructions':
        guidance, paths = load_instructions(workspace.root)
        ui.write(guidance or 'No root AGENTS.md or CODINE.md found. Nested instructions are not auto-loaded.')
    elif name == '/budget':
        if not session.billing:
            ui.write('No OpenRouter ledger yet. It starts before the first OpenRouter request; NVIDIA usage is tokens only.')
        else:
            ledger = Budget(session.billing)
            if argument:
                prefix, _, amount = argument.partition(' ')
                if prefix != 'reconcile' or not amount:
                    raise ValueError('Usage: /budget or /budget reconcile ACTUAL_USD_FROM_PROVIDER')
                ledger.settle(amount)
                agent.save()
            ui.write(ledger.summary())
    elif name == '/changes':
        from .changes import ChangeStore
        records = ChangeStore(workspace.root).list()
        ui.write('\n'.join(f"{row['id']}  {row['path']}" for row in records) or 'No file-tool checkpoints.')
        ui.write('Checkpoints cover file tools only. /undo refuses files changed since the checkpoint.')
    elif name == '/diff':
        ui.write(workspace.show_change(argument or None))
    elif name == '/undo':
        result = workspace.undo(argument or None)
        if result['status'] == 'undone':
            session.messages.append({'role': 'user', 'content': f"[Terminal action] I undid a file-tool change to {result['path']}. Read its current contents before further edits; older tool results describe the earlier state."})
            agent.save()
            ui.write('Undone ' + result['path'])
        else:
            ui.write(result.get('message', result['status'].capitalize()))
    elif name == '/details':
        ui.details()
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
            candidate = agent.store._read_session(argument)
            if candidate.provider != agent.config.provider:
                raise ValueError('Session uses another provider. Restart with --resume ID to select its provider.')
            if candidate.execution != session.execution:
                raise ValueError('Session uses different execution settings. Start codine --resume ID to restore them safely.')
            replacement = agent.store.load(argument)
        else:
            replacement = Session.create(workspace.root, session.model, SYSTEM, provider=session.provider)
            replacement.execution = dict(session.execution)
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
    elif name == '/context':
        ui.write(format_context(inspect_context(agent)))
    elif name == '/compact':
        changed = compact(session)
        agent.save()
        ui.write('Active context keeps the latest 3 complete turns; archive retained.' if changed else 'Already at 3 turns or fewer.')
    elif name == '/model':
        if agent.config.provider == 'openrouter' and ui.interactive and argument in ('', 'free'):
            from .router_catalog import metadata
            from model_picker import free_tool_models, select_model
            from .router_catalog import tool_models
            ui.write('Loading current OpenRouter catalog (no inference charge)…')
            records = metadata('models')
            if argument == 'free':
                choices = free_tool_models(records)
            else:
                available = {row['id'] for row in tool_models(records)}
                choices = [row for row in records if row.get('id') in available]
                choices.sort(key=lambda row: row['id'])
            if not choices:
                raise ValueError('No matching tool-capable models are available.')
            chosen = select_model(choices, session.model)
            if chosen is None:
                ui.write('Model unchanged.')
                return
            argument = chosen
        if argument == 'free':
            raise ValueError('/model free requires interactive OpenRouter mode.')
        if argument:
            if any(char.isspace() for char in argument) or len(argument) > 200:
                raise ValueError('Provide one model ID.')
            session.model = argument
            agent.save()
        ui.write(f'{agent.config.provider.upper()}: {session.model}\nModel must support the selected provider and tools; there is no automatic fallback.')
    elif name == '/thinking':
        agent.config.show_reasoning = not agent.config.show_reasoning
        ui.write('Reasoning display: ' + ('on' if agent.config.show_reasoning else 'off'))
    elif name == '/permissions':
        if argument not in ('ask', 'auto-edit', 'plan'):
            raise ValueError('Usage: /permissions ask|auto-edit|plan')
        if argument == 'auto-edit' and not ui.approve('Enable automatic ordinary file edits for this process? Core edits still require approval; commands follow /policy.'):
            return
        workspace.mode = argument
        ui.write('Permissions: ' + argument)
    elif name == '/restart':
        if not ui.interactive:
            raise ValueError('Restart requires interactive mode; one-shot runs should start a new process with --resume.')
        restart_process(agent, plan=workspace.mode == 'plan')
    else:
        raise ValueError('Unknown command. Use /help.')


def main(argv=None):
    args = parser().parse_args(argv)
    if args.json and not args.check and args.prompt is None and sys.stdin.isatty():
        parser().error('--json needs --prompt or piped stdin.')
    if args.check:
        count = check_sources()
        registry = build_registry(Workspace(APP_ROOT, lambda _: False), lambda: None)
        message = f'Startup check passed: {count} Python files, {len(registry.schemas())} tools. No API request or session write.'
        if args.json:
            JsonTerminal(Redactor()).emit('check', {'ok': True, 'message': message})
        else:
            print(message)
        return 0
    store = provider = None
    ui = JsonTerminal(Redactor()) if args.json else Terminal(Redactor())
    try:
        from .command_policy import CommandPolicy
        command_policy = CommandPolicy.load(args.policy)
        config = Config.load(args.project, provider=args.provider, model=args.model)
        redactor = Redactor.environment()
        ui.redactor = redactor
        store = SessionStore(config.project, redactor)
        resume = args.resume
        if args.continue_session:
            rows = store.list()
            resume = rows[0]['id'] if rows else None
        session = store.load(resume) if resume else Session.create(config.project, config.model, SYSTEM, provider=config.provider)
        if resume:
            if args.provider and args.provider != session.provider:
                raise ValueError('Saved provider differs from --provider. Start a new session to switch providers.')
            config = Config.load(config.project, provider=session.provider, model=args.model or session.model)
            if args.model:
                session.model = args.model
        execution = session.execution
        if not isinstance(execution, dict) or execution.get('executor', 'host') not in ('host', 'docker'):
            raise ValueError('Invalid saved executor settings; refusing to select host implicitly.')
        executor_name = args.executor or execution.get('executor', 'host')
        image = args.image or execution.get('image') or 'python:3.12-slim'
        command_executor = None
        if executor_name == 'docker':
            from .repository_executor import RepositoryExecutor
            command_executor = RepositoryExecutor(image=image, timeout=30)
        elif args.image:
            raise ValueError('--image requires --executor docker.')
        session.execution = {'executor': executor_name}
        if command_executor:
            session.execution['image'] = command_executor.image
        store.save(session)
        provider = OpenRouterProvider(config) if config.provider == 'openrouter' else NvidiaProvider(config)
        workspace = Workspace(config.project, ui.approve, ui.write, APP_ROOT, track_changes=True)
        workspace.command_executor = command_executor
        workspace.command_policy = command_policy
        workspace.policy_interactive = ui.interactive
        workspace.protected_paths = {command_policy.source.resolve()}
        if args.plan:
            workspace.mode = 'plan'
        agent = Agent(config, provider, None, store, session, ui.emit, redactor)
        agent.command_policy_path = str(command_policy.source) if args.policy else None
        agent.registry = build_registry(workspace, lambda: agent.request_restart(ui.approve))
        ui.state = lambda: {'project': workspace.root.name, 'mode': workspace.mode, 'tokens': agent.session.usage['total'], 'model': agent.session.model, 'executor': executor_name}
        ui.welcome(config.project, session.id, session.model)
        ui.write(execution_status(workspace))
        if command_policy.rules:
            ui.write(f'User command policy: {len(command_policy.rules)} rules. /policy explains decisions.')
        if not config.api_key:
            ui.write(f'No {config.provider.upper()}_API_KEY configured. Offline commands are available; model requests need a key.')
        one_shot = args.prompt
        if one_shot is None and not sys.stdin.isatty():
            one_shot = sys.stdin.read()
        def turn(text):
            try:
                outcome = agent.run_turn(text)
                if agent.restart_requested:
                    agent.restart_requested = False
                    restart_process(agent, plan=workspace.mode == 'plan')
                return outcome in ('completed', 'restart')
            except PersistenceError:
                raise
            except KeyboardInterrupt:
                agent.recover_interrupt()
                ui.emit('end_stream', '')
                ui.write('Turn interrupted. Completed results saved; unknown outcomes recorded. You can continue.')
                agent.finish('interrupted')
            except (APIError, OSError, ValueError, RuntimeError) as error:
                # Unexpected tool failure may leave a pending batch. Finalize without replay.
                agent.recover_interrupt()
                show_error(ui, error)
                agent.finish('failed')
            return False
        if one_shot is not None:
            if not one_shot.strip():
                return 0
            if one_shot.strip().startswith('/'):
                command(one_shot.strip(), agent, workspace, ui)
                agent.finish('command')
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
    except (OSError, ValueError, RuntimeError) as error:
        show_error(ui, error)
        ui.emit('run_end', {'outcome': 'failed'})
        return 1
    finally:
        if store:
            store.close()
        if provider:
            provider.close()


if __name__ == '__main__':
    sys.exit(main())
