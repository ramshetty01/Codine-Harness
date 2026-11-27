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
from .config import Config, Redactor
from .paths import eval_resource, STATE_ROOT
from .provenance import evaluation_manifest
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


def equivalent(actual, expected):
    # JSON booleans must not pass numeric cases simply because True == 1 in Python.
    if isinstance(expected, bool) or isinstance(actual, bool):
        return type(actual) is type(expected) and actual == expected
    if isinstance(expected, list):
        return isinstance(actual, list) and len(actual) == len(expected) and all(equivalent(a,b) for a,b in zip(actual,expected))
    if isinstance(expected, dict):
        return isinstance(actual, dict) and actual.keys() == expected.keys() and all(equivalent(actual[k],v) for k,v in expected.items())
    return actual == expected


def grade(root, cases, executor=None, manifest=None):
    if manifest is not None:
        from .sandbox import snapshot_files
        if executor is None:
            raise ValueError("Repository tasks require the Docker executor.")
        files = snapshot_files(root, manifest, "task.py")
        result = executor.evaluate_files(files, "task.py", [case["args"] for case in cases])
        return {**result, "passed": result["status"] == "completed" and equivalent(result.get("values"), [case["expected"] for case in cases])}

    if executor is not None:
        result = executor.evaluate((root / "task.py").read_text(), [case["args"] for case in cases])
        passed = result["status"] == "completed" and equivalent(result.get("values"), [case["expected"] for case in cases])
        return {**result, "passed": passed}

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
                 ('edit_file', {'path': 'task.py', 'old_text': self.task.get('initial', ''), 'new_text': self.task.get('reference', '')}),
                 ('check_python', {'path': 'task.py'})]
        if 'files' in self.task:
            steps = [('read_file', {'path': name}) for name in self.task['files']]
            for name, replacement in self.task['replacements'].items():
                steps.extend([('edit_file', {'path': name, 'old_text': self.task['files'][name], 'new_text': replacement}),
                              ('check_python', {'path': name})])
        if self.calls <= len(steps):
            name, args = steps[self.calls-1]
            return {'role': 'assistant', 'content': None, 'tool_calls': [
                {'id': f'call-{self.calls}', 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args)}}]}, 'tool_calls', None
        emit('text', 'Reference patch applied; independent grader decides success.')
        return {'role': 'assistant', 'content': 'Reference patch applied.'}, 'stop', None


def run_case(task, executor=None):
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix='codine-offline-eval-') as directory:
        root = Path(directory).resolve()
        source = root / 'task.py'
        manifest = task.get('files')
        if manifest:
            from .sandbox import validate_files
            validate_files(manifest, 'task.py')
            for name, content in manifest.items():
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content)
        else:
            source.write_text(task['initial'])
        broken = grade(root, task['cases'], executor, manifest)
        store = SessionStore(root)
        try:
            session = Session.create(root, 'scripted-oracle', SYSTEM)
            provider = ScriptedProvider(task)
            workspace = Workspace(root, lambda _: False, lambda _: None)
            workspace.mode = 'auto-edit'  # File edits only. Execution approvals remain denied.
            registry = build_registry(workspace, lambda: {'status': 'declined'})
            agent = Agent(Config(root), provider, registry, store, session, lambda *args: None, Redactor())
            outcome = agent.run_turn(task['prompt'])
            result = grade(root, task['cases'], executor, manifest)
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
    parser.add_argument('--executor', choices=('local', 'docker'), default='local', help='local is trusted-fixture-only; docker restricts candidate execution')
    parser.add_argument('--image', default='python:3.12-slim', help='Locally installed Docker image; never pulled automatically')
    parser.add_argument('--suite', choices=('functions', 'repository'), default='functions')
    args = parser.parse_args(argv)
    if args.suite == 'repository' and args.executor != 'docker':
        parser.error('--suite repository requires --executor docker')
    executor = None
    if args.executor == 'docker':
        from .sandbox import DockerSandbox
        executor = DockerSandbox(args.image)

    filename = 'repository_tasks.json' if args.suite == 'repository' else 'tasks.json'
    tasks = json.loads(eval_resource(filename).read_text())['tasks']
    selected = [task for task in tasks if args.split == 'all' or task['split'] == args.split]
    if not selected:
        parser.error('No tasks match the selected suite and split.')
    provenance = evaluation_manifest(selected, mode='offline-scripted', image=executor.image if executor else None, config=Config(Path.cwd()))
    results = [run_case(task, executor) for task in selected]
    report = {'version': 2, 'provenance': provenance, 'suite': args.suite, 'mode': 'offline-scripted', 'executor': args.executor, 'image': executor.image if executor else None, 'inference_requests': 0, 'inference_cost_usd': '0',
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
