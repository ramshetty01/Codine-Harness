"""Descriptive evaluation metrics with explicit missing and unattempted data."""
from decimal import Decimal
from .budget import money
import math
import statistics


def summarize_results(results, selected_tasks, known_cost, pending=None, *, blocked=False):
    selected = list(selected_tasks)
    if len(set(selected)) != len(selected):
        raise ValueError('Selected task IDs must be unique.')
    attempted = [row['task'] for row in results]
    if len(set(attempted)) != len(attempted) or set(attempted) - set(selected):
        raise ValueError('Results must be a unique subset of selected tasks.')
    if any(type(row.get('passed')) is not bool for row in results):raise ValueError('Explicit boolean task outcomes required.')
    passed = sum(row['passed'] is True for row in results)
    if any(type(row.get('seconds')) not in (int,float) for row in results):raise ValueError('Task durations must be numeric, not inferred.')
    durations = sorted(float(row['seconds']) for row in results)
    if any(not math.isfinite(value) or value < 0 for value in durations):
        raise ValueError('Durations must be finite and nonnegative.')
    cost = money(known_cost) if known_cost is not None else None
    if cost is not None and (not cost.is_finite() or cost < 0):
        raise ValueError('Known spend must be finite and nonnegative.')
    def counted(name):
        values = [row.get(name) for row in results]
        known = [value for value in values if type(value) is int and value >= 0]
        return {'count': sum(known), 'unknown_tasks': len(values) - len(known)}
    return {'selected': len(selected), 'attempted': len(results), 'passed': passed,
            'failed': len(results) - passed, 'unattempted': [name for name in selected if name not in attempted],
            'success_rate_selected': passed / len(selected) if selected else None,
            'success_rate_attempted': passed / len(results) if results else None,
            'median_task_seconds': statistics.median(durations) if durations else None,
            'p95_task_seconds': durations[math.ceil(len(durations) * .95) - 1] if durations else None,
            'known_cost_usd': str(cost) if cost is not None else None, 'cost_complete': cost is not None and pending is None and not blocked,
            'cost_per_solved_usd': str(cost / passed) if passed and cost is not None and pending is None and not blocked else None,
            'tool_failures': counted('tool_failures'), 'interventions': counted('interventions'),
            'incorrect_edits': counted('incorrect_edits'),
            'note': 'A failed task is not automatically an incorrect edit. Missing counters remain unknown. Regressions require paired reports.'}
