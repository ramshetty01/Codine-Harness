"""A checked process replacement loads new Python code and tool definitions."""
import ast
from contextlib import nullcontext
import os
from pathlib import Path
import subprocess
import sys

from .config import APP_ROOT


def check_sources(app_root=APP_ROOT):
    root = Path(app_root)
    files = sorted((root / 'codine').glob('*.py'))
    if (root / 'main.py').is_file():
        files.insert(0, root / 'main.py')
    if not (root / 'codine' / 'cli.py').is_file():
        raise ValueError('Missing codine/cli.py.')
    for path in files:
        try:
            ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
        except SyntaxError as error:
            raise ValueError(f'Syntax error in {path.name}:{error.lineno}: {error.msg}; restart cancelled.') from error
    return len(files)


def restart_process(agent, app_root=APP_ROOT, execute=os.execv, plan=False):
    root = Path(app_root).resolve()
    agent.save()
    check_sources(root)
    # New interpreter imports updated code; no credentials, model call, or session mutation.
    entry = [str(root / 'main.py')] if (root / 'main.py').is_file() else ['-m', 'codine']
    probe = subprocess.run([sys.executable, *entry, '--check'], cwd=agent.session.project,
                           capture_output=True, text=True, timeout=20, stdin=subprocess.DEVNULL)
    if probe.returncode:
        raise ValueError('Startup check failed. Current process kept running.\n' + (probe.stderr or probe.stdout)[-3000:])
    argv = [sys.executable, *entry, '--project', agent.session.project, '--resume', agent.session.id]
    if agent.session.provider == 'custom':
        from .custom_endpoint import match_session
        match_session(agent.config,agent.session)
        argv.extend(['--provider','custom','--endpoint',agent.config.custom_endpoint,'--allow-unmetered-custom'])
    execution = getattr(agent.session, 'execution', {})
    if execution:
        if execution.get('executor') not in ('host', 'docker'):
            raise ValueError('Invalid saved executor; restart cancelled.')
        argv.extend(['--executor', execution['executor']])
        if execution['executor'] == 'docker':
            if not execution.get('image'):
                raise ValueError('Missing Docker image; restart cancelled.')
            argv.extend(['--image', execution['image']])
    if getattr(agent, 'command_policy_path', None):
        argv.extend(['--policy', agent.command_policy_path])
    if getattr(agent, 'execution_profile_path', None):
        argv.extend(['--execution-profile', agent.execution_profile_path])
    if plan:
        argv.append('--plan')
    if getattr(agent, 'jobs', None) is not None:
        agent.jobs.cancel_all()
    if getattr(agent, 'extension_manager', None):
        agent.extension_manager.close()
    if getattr(agent, 'mcp_manager', None):
        agent.mcp_manager.close()
    agent.emit('status', 'Session saved. Restarting to load edited code and tool definitions; restrictive planning is retained when selected; auto-edit resets to ask.')
    agent.store.close()  # Release advisory lock before replacement; descriptors are noninheritable.
    try:
        captured = getattr(agent, 'execution_credentials', None)
        with captured.restart_environment() if captured is not None else nullcontext():
            execute(sys.executable, argv)
    finally:
        # Only reached if exec fails (or a test injects a fake executor).
        agent.store.acquire(agent.session.id)
