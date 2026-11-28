"""Opt-in NVIDIA integration check; may consume provider quota.
Run: .venv/bin/python tests/live_smoke.py
Only approved writes/commands in a disposable temporary project are expected.
"""
from pathlib import Path
import sys
import tempfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from codine.agent import Agent, SYSTEM
from codine.config import Config, Redactor
from codine.provider import NvidiaProvider
from codine.sessions import Session, SessionStore, validate_messages
from codine.tools import build_registry
from codine.workspace import Workspace
import json


def main():
    with tempfile.TemporaryDirectory(prefix='codine-live-') as directory:
        root = Path(directory)
        (root / 'README.md').write_text('Smoke fixture: project marker is TANGERINE-73.\n')
        config = Config.load(root)
        config.max_steps = 8
        config.max_tokens = 4096
        if not config.api_key:
            raise SystemExit('BLOCKED: NVIDIA_API_KEY is not configured.')
        redactor = config.redactor()
        store = SessionStore(root, redactor)
        session = Session.create(root, config.model, SYSTEM)
        provider = NvidiaProvider(config)
        events = []
        def emit(kind, text):
            if kind in ('tool', 'status'):
                print(redactor.text(f'{kind}: {text}'), flush=True)
            events.append((kind, text))
        workspace = Workspace(root, lambda _: True, lambda _: None)
        agent = Agent(config, provider, build_registry(workspace, lambda: {'status': 'declined'}), store, session, emit, redactor)
        try:
            agent.run_turn('In this disposable smoke-test project, read README.md. Create hello.py containing exactly print("Hello, Codine!") with a trailing newline. Check its Python syntax using check_python, then run it using run_command with argv ["python", "hello.py"]. Finish by reporting the README marker and observed stdout. Do not restart or modify anything else.')
            validate_messages(session.messages)
            assert (root / 'hello.py').read_text() == 'print("Hello, Codine!")\n'
            names = [text for kind, text in events if kind == 'tool']
            assert {'read_file', 'write_file', 'check_python', 'run_command'} <= set(names), names
            results = [json.loads(message['content']) for message in session.messages if message['role'] == 'tool']
            assert any(result.get('stdout') == 'Hello, Codine!\n' and result.get('exit_code') == 0 for result in results)
            assert 'TANGERINE-73' in session.messages[-1]['content']
            assert store.load(session.id).messages == session.messages
            print('PASS: real NVIDIA read → create → syntax → approved execution → final answer → saved resume.', flush=True)
            print('Usage:', session.usage, flush=True)
        finally:
            provider.close()
            store.close()


if __name__ == '__main__':
    main()
