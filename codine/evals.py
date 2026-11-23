"""Offline integration exercises, NOT a measurement of model capability.

Run: python -m codine.evals --offline --report .codine/evals/offline.json
The scripted oracle and behavioral grader stay outside the agent workspace.
No live provider is imported or instantiated here.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from .agent import Agent, SYSTEM
from .config import APP_ROOT, Config, Redactor
from .sessions import Session, SessionStore, validate_messages
from .tools import build_registry
from .workspace import Workspace

GRADER = '''
import json, runpy, sys
module = runpy.run_path(sys.argv[1])
cases = json.loads(sys.argv[2])
for case in cases:
    actual = module['solve'](*case['args'])
    if actual != case['expected']:
        raise AssertionError(f"Expected {case['expected']!r}, got {actual!r}")
print('PASS')
'''


def grade(root, cases):
    try:
        result = subprocess.run([sys.executable, '-I', '-c', GRADER, str(root / 'task.py'), json.dumps(cases)],
            cwd=root, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=3,
            env={key: os.environ[key] for key in ('PATH', 'LANG', 'TMPDIR') if key in os.environ})
        return {'passed': result.returncode == 0, 'exit_code': result.returncode,
                'diagnostic': (result.stderr or result.stdout)[-1500:]}
    except subprocess.TimeoutExpired:
        return {'passed': False, 'exit_code': None, 'diagnostic': 'Grader timeout'}


class ScriptedProvider:
    def __init__(self, task):
        self.task = task
        self.calls = 0

    def complete(self, messages, schemas, model, emit):
        validate_messages(messages)
        self.calls += 1
        steps = [('read_file', {'path': 'task.py'}),
                 ('edit_file', {'path': 'task.py', 'old_text': self.task['initial'], 'new_text': self.task['reference']}),
                 ('check_python', {'path': 'task.py'})]
        if self.calls <= len(steps):
            name, args = steps[self.calls-1]
            return {'role': 'assistant', 'content': None, 'tool_calls': [
                {'id': f'call-{self.calls}', 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args)}}]}, 'tool_calls', None
        emit('text', 'Reference patch applied; independent grader decides success.')
        return {'role': 'assistant', 'content': 'Reference patch applied.'}, 'stop', None


def run_case(task):
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix='codine-offline-eval-') as directory:
        root = Path(directory).resolve()
        source = root / 'task.py'
        source.write_text(task['initial'])
        broken = grade(root, task['cases'])
        store = SessionStore(root)
        try:
            session = Session.create(root, 'scripted-oracle', SYSTEM)
            provider = ScriptedProvider(task)
            workspace = Workspace(root, lambda _: False, lambda _: None)
            workspace.mode = 'auto-edit'  # File edits only. Execution approvals remain denied.
            registry = build_registry(workspace, lambda: {'status': 'declined'})
            agent = Agent(Config(root), provider, registry, store, session, lambda *args: None, Redactor())
            outcome = agent.run_turn(task['prompt'])
            result = grade(root, task['cases'])
            saved = store.load(session.id)
            validate_messages(saved.messages)
            return {'task': task['id'], 'split': task['split'], 'outcome': outcome,
                    'passed': outcome == 'completed' and result['passed'] and not broken['passed'],
                    'broken_fixture_rejected': not broken['passed'], 'reference_grade': result,
                    'scripted_calls': provider.calls, 'seconds': round(time.monotonic()-started, 3)}
        finally:
            store.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--offline', action='store_true', required=True, help='Required: only scripted/offline exercises exist in this release')
    parser.add_argument('--report', default='.codine/evals/offline.json')
    parser.add_argument('--split', choices=('all', 'development', 'holdout'), default='all')
    args = parser.parse_args(argv)
    tasks = json.loads((APP_ROOT / 'evals/tasks.json').read_text())['tasks']
    results = [run_case(task) for task in tasks if args.split == 'all' or task['split'] == args.split]
    report = {'version': 1, 'mode': 'offline-scripted', 'inference_requests': 0, 'inference_cost_usd': '0',
              'meaning': 'Harness/grader verification only; NOT model solve rate.',
              'passed': sum(row['passed'] for row in results), 'total': len(results), 'results': results}
    path = Path(args.report)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2)+'\n')
    print(f"Offline exercises: {report['passed']}/{report['total']}. Inference calls: 0. Report: {path}")
    print(report['meaning'])
    return 0 if report['passed'] == report['total'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
