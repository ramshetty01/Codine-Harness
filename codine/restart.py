"""A checked process replacement loads new Python code and tool definitions."""
import ast
import os
from pathlib import Path
import subprocess
import sys

from .config import APP_ROOT


def check_sources(app_root=APP_ROOT):
    root = Path(app_root)
    files = [root / 'main.py'] + sorted((root / 'codine').glob('*.py'))
    if not (root / 'codine' / 'cli.py').is_file():
        raise ValueError('Missing codine/cli.py.')
    for path in files:
        try:
            ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
        except SyntaxError as error:
            raise ValueError(f'Syntax error in {path.name}:{error.lineno}: {error.msg}; restart cancelled.') from error
    return len(files)


def restart_process(agent, app_root=APP_ROOT, execute=os.execv):
    root = Path(app_root).resolve()
    agent.save()
    check_sources(root)
    # New interpreter imports updated code; no credentials, model call, or session mutation.
    probe = subprocess.run([sys.executable, str(root / 'main.py'), '--check'], cwd=root,
                           capture_output=True, text=True, timeout=20, stdin=subprocess.DEVNULL)
    if probe.returncode:
        raise ValueError('Startup check failed. Current process kept running.\n' + (probe.stderr or probe.stdout)[-3000:])
    argv = [sys.executable, str(root / 'main.py'), '--project', agent.session.project, '--resume', agent.session.id]
    agent.emit('status', 'Session saved. Restarting to load edited code and tool definitions; approvals return to ask mode.')
    agent.store.close()  # Release advisory lock before replacement; descriptors are noninheritable.
    try:
        execute(sys.executable, argv)
    finally:
        # Only reached if exec fails (or a test injects a fake executor).
        agent.store.acquire(agent.session.id)
