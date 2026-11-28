"""Compare repeated evaluation reports without making model requests.

python -m codine.compare_evals --baseline before1.json before2.json --candidate after1.json after2.json
"""
import argparse
from decimal import Decimal
import json
from pathlib import Path
import statistics
import re
from .eval_metrics import summarize_results
from .eval_attempts import AttemptReport

from .provenance import fingerprint
from .budget import money


def compare_reports(baseline, candidate):
    if max(len(baseline),len(candidate))>20:raise ValueError('At most 20 retained attempts per condition.')
    if min(len(baseline), len(candidate)) < 2:
        raise ValueError('At least two independent reports per condition are required.')
    conditions = []
    control = None
    attempts = set()
    for reports in (baseline, candidate):
        source = None
        runs = []
        for report in reports:
            if not isinstance(report,dict):raise ValueError('Each report must be an object.')
            manifest = report.get('provenance', {})
            if not isinstance(manifest,dict) or manifest.get('version') != 2:
                raise ValueError('Reports need version-2 provenance; legacy reports remain viewable but are not controlled comparisons.')
            attempt=report.get('attempt',{})
            if not isinstance(attempt,dict):raise ValueError('Invalid attempt envelope.')
            if any(not isinstance(manifest.get(key),dict) for key in ('source','configuration','runtime','controls')) or not isinstance(manifest.get('tasks'),list):raise ValueError('Malformed provenance sections.')
            ident=attempt.get('id')
            if not isinstance(ident,str) or not re.fullmatch('[a-f0-9]{32}',ident) or ident in attempts:
                raise ValueError('Each independent attempt needs a unique retained attempt ID; copies do not count as repeats.')
            attempts.add(ident)
            if attempt.get('status') not in ('running','completed','aborted'):raise ValueError('Invalid attempt status.')
            if not isinstance(report.get('results'),list):raise ValueError('Report results must be a list.')
            components = {key: manifest[key] for key in ('source', 'tasks', 'configuration', 'runtime', 'controls')}
            if manifest.get('sha256') != fingerprint(components):
                raise ValueError('Manifest integrity check failed.')
            for name,key in (('source','files'),('configuration','values'),('controls','values')):
                if manifest[name].get('sha256') != fingerprint(manifest[name][key]):raise ValueError('Nested provenance integrity check failed.')
            if manifest['configuration']['values'].get('mode')=='live' and manifest['controls']['values'].get('request_settings') is None:
                raise ValueError('Live comparisons require recorded request settings.')
            signature = {key: manifest[key] for key in ('tasks', 'configuration', 'runtime', 'controls')}
            if control is None:
                control = signature
            if signature != control:
                raise ValueError('Task selection, model/request settings, and execution runtime must match.')
            if source is None:
                source = manifest['source']['sha256']
            if source != manifest['source']['sha256']:
                raise ValueError('Source identity must remain fixed within each condition.')
            task_ids = [task['id'] for task in manifest['tasks']]
            results = report.get('results', [])
            if len(set(task_ids))!=len(task_ids):raise ValueError('Selected tasks must be unique.')
            result_ids=[row['task'] for row in results]
            if len(set(result_ids))!=len(result_ids) or set(result_ids)-set(task_ids):raise ValueError('Results must be a unique subset of selected tasks.')
            if any(type(row.get('passed')) is not bool for row in results):raise ValueError('Each result needs an explicit boolean passed value.')
            if any(type(row.get('seconds')) not in (int,float) for row in results):raise ValueError('Task durations must be numeric.')
            durations = [float(row['seconds']) for row in results]
            if any(not 0 <= value < float('inf') for value in durations):
                raise ValueError('Task durations must be finite and nonnegative.')
            cost = report.get('cost_usd', report.get('inference_cost_usd'))
            known_cost=cost
            uncertain=report.get('pending_usd') is not None or bool(report.get('blocked'))
            if uncertain:cost=None
            if cost is not None:
                cost = money(cost)
                if not cost.is_finite() or cost < 0:
                    raise ValueError('Cost must be finite and nonnegative.')
            runs.append({'attempt_id':ident,'status':attempt['status'],
                         'passed': sum(row['passed'] for row in results), 'total': len(task_ids),
                         'metrics':summarize_results(results,task_ids,known_cost,report.get('pending_usd'),blocked=bool(report.get('blocked'))),
                         'unattempted':[task for task in task_ids if task not in result_ids],
                         'seconds':sum(durations) if len(results)==len(task_ids) and attempt['status']=='completed' else None,
                         'measured_task_seconds':sum(durations), 'cost_usd': str(cost) if cost is not None else None,
                         'tasks': {task:next((row['passed'] for row in results if row['task']==task),None) for task in task_ids}})
        counts = [run['passed'] for run in runs]
        timed=[run['seconds'] for run in runs if run['seconds'] is not None]
        costs = [Decimal(run['metrics']['known_cost_usd']) for run in runs if run['metrics']['known_cost_usd'] is not None]
        conditions.append({'source_sha256': source, 'runs': runs,
                           'mean_passed': statistics.mean(counts), 'min_passed': min(counts),
                           'max_passed': max(counts), 'stdev_passed': statistics.stdev(counts),
                           'mean_seconds': statistics.mean(timed) if timed else None,
                           'stdev_seconds': statistics.stdev(timed) if len(timed)>1 else None,
                           'unknown_duration_runs':len(runs)-len(timed),
                           'aborted_or_incomplete_runs':sum(run['status']!='completed' or bool(run['unattempted']) for run in runs),
                           'known_cost_usd': str(sum(costs, Decimal(0))) if costs else None,
                           'cost_per_solved_usd':str(sum(costs,Decimal(0))/sum(counts)) if sum(counts) and all(run['cost_usd'] is not None for run in runs) else None,
                           'unknown_cost_runs': sum(run['cost_usd'] is None for run in runs)})
    before, after = conditions
    changes = []
    for task in control['tasks']:
        task_id = task['id']
        old = sum(run['tasks'][task_id] is True for run in before['runs']) / len(before['runs'])
        new = sum(run['tasks'][task_id] is True for run in after['runs']) / len(after['runs'])
        changes.append({'task': task_id, 'baseline_pass_rate': old, 'candidate_pass_rate': new,
                        'delta': new - old,'regression':new<old,
                        'baseline_unattempted':sum(run['tasks'][task_id] is None for run in before['runs']),
                        'candidate_unattempted':sum(run['tasks'][task_id] is None for run in after['runs'])})
    return {'version': 2, 'baseline': before, 'candidate': after, 'task_changes': changes,
            'regressed_tasks':[row['task'] for row in changes if row['regression']],
            'improved_tasks':[row['task'] for row in changes if row['delta']>0],
            'meaning': 'Descriptive repeated-run comparison, not statistical proof or a claim of model parity.'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', nargs='+', required=True)
    parser.add_argument('--candidate', nargs='+', required=True)
    parser.add_argument('--report', help='New output path; existing files are never overwritten')
    args = parser.parse_args(argv)
    try:
        paths = [Path(name).resolve() for name in args.baseline + args.candidate]
        if len(set(paths)) != len(paths):
            raise ValueError('Each run must use a distinct report file.')
        total_bytes=0
        def load(names):
            nonlocal total_bytes
            if len(names)>20:raise ValueError('At most 20 attempts per condition.')
            rows = []
            for name in names:
                path = Path(name)
                if path.stat().st_size > 20_000_000:
                    raise ValueError('Report exceeds 20MB limit.')
                with path.open('rb') as stream:raw=stream.read(20_000_001)
                total_bytes+=len(raw)
                if len(raw)>20_000_000 or total_bytes>64_000_000:raise ValueError('Comparison input byte limit exceeded.')
                rows.append(json.loads(raw))
            return rows
        result = compare_reports(load(args.baseline), load(args.candidate))
    except (ValueError, KeyError, TypeError, OSError, RecursionError) as error:
        parser.error(str(error))
    try:
        writer=AttemptReport(args.report,prefix='comparison')
        writer.save(result,status='completed')
        output=writer.path
    except (OSError,ValueError) as error:parser.error(str(error))
    print(f"Mean tasks passed: {result['baseline']['mean_passed']} → {result['candidate']['mean_passed']}. Report: {output}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
