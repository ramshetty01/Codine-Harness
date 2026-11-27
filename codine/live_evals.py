"""Explicit, bounded live OpenRouter evaluations; candidate execution is Docker-only."""
import argparse
from dataclasses import replace
import json
import math
import statistics
from pathlib import Path
import tempfile
import time

from .agent import Agent, SYSTEM
from .budget import Budget, BudgetError, money
from .config import Config, Redactor
from .paths import eval_resource, STATE_ROOT
from .provenance import evaluation_manifest
from .eval_budget import EvaluationBudget
from .eval_metrics import summarize_results
from .evals import grade, task_cases
from .provider import OpenRouterProvider
from .registry import ToolSpec
from .router_catalog import metadata, tool_models
from .sandbox import DockerSandbox, validate_files
from .sessions import Session, SessionStore, validate_messages
from .tools import build_registry
from .workspace import Workspace


class GuardedProvider:
    def __init__(self, provider, shared, run_budget, key, read_metadata=metadata):
        self.provider, self.shared, self.run_budget = provider, shared, run_budget
        self.key, self.read_metadata = key, read_metadata
        self.calls = 0
        self.durations = []

    def complete(self, messages, schemas, model, emit):
        # Conservative byte guard, not an exact tokenizer or a remote dollar cap.
        if len(json.dumps([messages, schemas], ensure_ascii=False).encode()) > 30000:
            raise BudgetError('Evaluation request exceeds the 30 KB input allowance.')
        remaining = self.read_metadata('key', self.key).get('limit_remaining')
        if remaining is None or money(remaining) < money('1.55'):
            raise BudgetError('Key must have at least $1.55 remaining to preserve the $1.50 reserve.')
        self.run_budget.reserve()
        self.shared.reserve()  # Durable reservation precedes all model traffic.
        self.calls += 1
        started = time.monotonic()
        try:
            result = self.provider.complete(messages, schemas, model, emit)
            cost = getattr(result[2], 'cost', None)
            if cost is None:
                raise BudgetError('Unknown request charge. Shared reservation retained; no retry allowed.')
            self.shared.settle(cost)
            blocked = self.run_budget.settle(cost)
            if blocked:
                raise BudgetError(blocked)
            return result
        finally:
            self.durations.append(round(time.monotonic() - started, 3))


def evaluate_task(task, base_config, executor, shared, run_budget):
    public, hidden = task_cases(task)
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix='codine-live-eval-') as directory:
        root = Path(directory).resolve()
        files = task.get('files', {'task.py': task.get('initial', '')})
        validate_files(files, 'task.py')
        for name, content in files.items():
            path = root / name
            report['metrics'] = summarize_results(report['results'], report['selected_tasks'], report['cost_usd'], report['pending_usd'])
        path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        # Do not instantiate ScriptedProvider or pass reference edits into model context.
        config = replace(base_config, project=root)
        redactor = Redactor([config.api_key])
        store = SessionStore(root, redactor)
        transport = OpenRouterProvider(config)
        provider = GuardedProvider(transport, shared, run_budget, config.api_key)
        events = []
        session = Session.create(root, config.model, SYSTEM, provider='openrouter')
        workspace = Workspace(root, lambda _: False, lambda _: None)
        workspace.mode = 'auto-edit'
        registry = build_registry(workspace, lambda: {'status': 'declined'})
        # Host command execution/restart are unavailable even to hallucinated calls.
        del registry.tools['run_command']
        del registry.tools['request_restart']
        def check():
            return grade(root, public, executor, files)
        registry.register(ToolSpec('run_checks', 'Run public behavior checks in a disposable Docker container. Final grading also uses held-back checks after completion. Returns public actual values and pass/fail; no host commands.',
            {'type': 'object', 'properties': {}, 'additionalProperties': False}, check, 'execute'))
        agent = Agent(config, provider, registry, store, session, lambda kind, value: events.append((kind, value)), redactor)
        error = None
        try:
            outcome = agent.run_turn(task['prompt'] + '\nWork on the actual files. Use run_checks to verify behavior before finishing. Host commands are unavailable.')
        except Exception as failure:
            outcome, error = 'error', redactor.text(str(failure))[:1000]
        try:
            result = grade(root, public + hidden, executor, files)
            saved = store.load(session.id)
            validate_messages(saved.messages)
            tool_errors = 0
            for kind, content in events:
                if kind == 'result':
                    try:
                        data = json.loads(content)
                        tool_errors += bool(isinstance(data, dict) and (data.get('error') or data.get('passed') is False))
                    except ValueError:
                        pass
            return {'task': task['id'], 'split': task['split'], 'outcome': outcome,
                    'passed': outcome == 'completed' and result['passed'], 'grade': result,
                    'public_cases': len(public), 'hidden_cases': len(hidden),
                    'error': error, 'requests': provider.calls, 'request_seconds': provider.durations,
                    'cost_usd': session.billing.get('spent', '0'), 'tool_failures': tool_errors,
                    'seconds': round(time.monotonic() - started, 3),
                    'messages': redactor.data(session.messages),
                    'candidate_files': {name: redactor.text((root / name).read_text()) for name in files}}
        finally:
            transport.close()
            store.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model')
    parser.add_argument('--reconcile-cost', help='Explicitly settle the shared pending request using its verified provider charge; sends no inference')
    parser.add_argument('--suite', choices=('functions', 'repository'), default='functions')
    parser.add_argument('--task', action='append', help='Exact task ID; repeat to select several')
    parser.add_argument('--run-budget', default='0.25', help='USD cap for this invocation (0.05–0.75)')
    parser.add_argument('--report', default='.codine/evals/live.json')
    parser.add_argument('--image', default='python:3.12-slim')
    args = parser.parse_args(argv)
    cap = money(args.run_budget)
    if not money('0.05') <= cap <= money('0.75'):
        parser.error('Run budget must be $0.05–$0.75. Shared lifetime limit is $3.50.')
    if not args.model and args.reconcile_cost is None:
        parser.error('--model is required for live evaluation.')
    cfg = Config.load(Path.cwd(), provider='openrouter', model=args.model or 'reconciliation-only')
    if not cfg.api_key:
        parser.error('Set OPENROUTER_API_KEY in .env; never paste it into command arguments.')
    if args.reconcile_cost is not None:
        shared = EvaluationBudget(STATE_ROOT / 'evals/budget.json', cfg.api_key)
        try:
            shared.settle(args.reconcile_cost)
            print('Shared evaluation ledger reconciled. ' + shared.ledger.summary())
        finally:
            shared.close()
        return 0
    catalog = {row['id']: row for row in tool_models(metadata('models'))}
    row = catalog.get(args.model)
    if row is None or money(row['prompt']) > money('0.000001') or money(row['completion']) > money('0.000002'):
        parser.error('Select an available tool model priced at most $1/M input and $2/M output.')
    cfg = replace(cfg, max_steps=12, max_tokens=2048, context_chars=24000,
                  budget_usd=str(cap), request_budget_usd='0.05',
                  router_max_price={'prompt': 1, 'completion': 2, 'request': 0})
    tasks_file = 'repository_tasks.json' if args.suite == 'repository' else 'tasks.json'
    tasks = json.loads(eval_resource(tasks_file).read_text())['tasks']
    selected = [task for task in tasks if not args.task or task['id'] in args.task]
    if not selected or (args.task and set(args.task) - {task['id'] for task in selected}):
        parser.error('Unknown or empty task selection.')
    executor = DockerSandbox(args.image)
    shared = EvaluationBudget(STATE_ROOT / 'evals/budget.json', cfg.api_key)
    run_budget = Budget.create(str(cap), '0.05')
    provenance = evaluation_manifest(selected, mode='live', image=executor.image, config=cfg)
    report = {'version': 2, 'provenance': provenance, 'mode': 'live', 'model': cfg.model, 'suite': args.suite,
              'image': executor.image, 'catalog_pricing': row, 'run_limit_usd': str(cap),
              'selected_tasks': [task['id'] for task in selected], 'results': []}
    path = Path(args.report)
    def save_report():
        report.update(cost_usd=run_budget.state['spent'], pending_usd=shared.ledger.state['pending'],
                      shared_cost_usd=shared.ledger.state['spent'],
                      passed=sum(r['passed'] for r in report['results']), total=len(report['results']))
        durations = sorted(r['seconds'] for r in report['results'])
        report['median_task_seconds'] = statistics.median(durations) if durations else None
        report['p95_task_seconds'] = durations[math.ceil(len(durations) * .95) - 1] if durations else None
        report['cost_per_solved_usd'] = str(money(report['cost_usd']) / report['passed']) if report['passed'] else None
        report['requests'] = sum(r['requests'] for r in report['results'])
        report['tool_failures'] = sum(r['tool_failures'] for r in report['results'])
        report['metrics'] = summarize_results(report['results'], report['selected_tasks'], report['cost_usd'], report['pending_usd'])
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(report, indent=2) + '\n')
        temporary.replace(path)
    try:
        for task in selected:
            if (shared.ledger.state['pending'] is not None or shared.ledger.state['blocked'] or
                    money(run_budget.state['spent']) + money('0.05') > cap):
                break
            print(f"Evaluating {task['id']} with {cfg.model} …", flush=True)
            result = evaluate_task(task, cfg, executor, shared, run_budget)
            report['results'].append(result)
            save_report()
            print(f"  {'PASS' if result['passed'] else 'FAIL'} · {result['requests']} requests · {result['seconds']}s · run cost ${report['cost_usd']}", flush=True)
            if result['error']:
                print('  ' + result['error'], flush=True)
                break
    finally:
        save_report()
        shared.close()
    print(f"Live result: {report['passed']}/{report['total']}; ${report['cost_usd']}. Report: {path}")
    return 0 if report['passed'] == len(selected) else 1


if __name__ == '__main__':
    raise SystemExit(main())
