"""Compare repeated evaluation reports without making model requests.

python -m codine.compare_evals --baseline before1.json before2.json --candidate after1.json after2.json
"""
import argparse
from decimal import Decimal
import json
from pathlib import Path
import statistics

from .provenance import fingerprint


def compare_reports(baseline, candidate):
    if min(len(baseline), len(candidate)) < 2:
        raise ValueError('At least two independent reports per condition are required.')
    conditions = []
    control = None
    for reports in (baseline, candidate):
        source = None
        runs = []
        for report in reports:
            manifest = report.get('provenance', {})
            if manifest.get('version') != 1:
                raise ValueError('Reports need version-1 provenance; historical untracked reports cannot be compared.')
            components = {key: manifest[key] for key in ('source', 'tasks', 'configuration', 'runtime')}
            if manifest.get('sha256') != fingerprint(components):
                raise ValueError('Manifest integrity check failed.')
            signature = {key: manifest[key] for key in ('tasks', 'configuration', 'runtime')}
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
            if (len(set(task_ids)) != len(task_ids) or
                sorted(row['task'] for row in results) != sorted(task_ids)):
                raise ValueError('Every run must contain exactly one result for each selected task.')
            if any(type(row.get('passed')) is not bool for row in results):
                raise ValueError('Each result needs an explicit boolean passed value.')
            durations = [float(row['seconds']) for row in results]
            if any(not 0 <= value < float('inf') for value in durations):
                raise ValueError('Task durations must be finite and nonnegative.')
            cost = report.get('cost_usd', report.get('inference_cost_usd'))
            if report.get('pending_usd') is not None or report.get('blocked'):
                cost = None
            if cost is not None:
                cost = Decimal(str(cost))
                if not cost.is_finite() or cost < 0:
                    raise ValueError('Cost must be finite and nonnegative.')
            runs.append({'passed': sum(row['passed'] for row in results), 'total': len(results),
                         'seconds': sum(durations), 'cost_usd': str(cost) if cost is not None else None,
                         'tasks': {row['task']: row['passed'] for row in results}})
        counts = [run['passed'] for run in runs]
        costs = [Decimal(run['cost_usd']) for run in runs if run['cost_usd'] is not None]
        conditions.append({'source_sha256': source, 'runs': runs,
                           'mean_passed': statistics.mean(counts), 'min_passed': min(counts),
                           'max_passed': max(counts), 'stdev_passed': statistics.stdev(counts),
                           'mean_seconds': statistics.mean(run['seconds'] for run in runs),
                           'known_cost_usd': str(sum(costs, Decimal(0))),
                           'unknown_cost_runs': len(runs) - len(costs)})
    before, after = conditions
    changes = []
    for task in control['tasks']:
        task_id = task['id']
        old = sum(run['tasks'][task_id] for run in before['runs']) / len(before['runs'])
        new = sum(run['tasks'][task_id] for run in after['runs']) / len(after['runs'])
        changes.append({'task': task_id, 'baseline_pass_rate': old, 'candidate_pass_rate': new,
                        'delta': new - old})
    return {'version': 1, 'baseline': before, 'candidate': after, 'task_changes': changes,
            'meaning': 'Descriptive repeated-run comparison, not statistical proof or a claim of model parity.'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', nargs='+', required=True)
    parser.add_argument('--candidate', nargs='+', required=True)
    parser.add_argument('--report', default='.codine/evals/comparison.json')
    args = parser.parse_args(argv)
    try:
        paths = [Path(name).resolve() for name in args.baseline + args.candidate]
        if len(set(paths)) != len(paths):
            raise ValueError('Each run must use a distinct report file.')
        def load(names):
            rows = []
            for name in names:
                path = Path(name)
                if path.stat().st_size > 20_000_000:
                    raise ValueError('Report exceeds 20MB limit.')
                rows.append(json.loads(path.read_text()))
            return rows
        result = compare_reports(load(args.baseline), load(args.candidate))
    except (ValueError, KeyError, TypeError, OSError) as error:
        parser.error(str(error))
    output = Path(args.report)
    if output.resolve() in paths:
        parser.error('Output must not overwrite an input report.')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + '\n')
    print(f"Mean tasks passed: {result['baseline']['mean_passed']} → {result['candidate']['mean_passed']}. Report: {output}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
