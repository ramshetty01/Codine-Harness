import unittest
from codine.eval_metrics import summarize_results


class MetricsTests(unittest.TestCase):
    def test_partial_run_and_unknowns(self):
        rows = [{'task': 'a', 'passed': True, 'seconds': 2, 'tool_failures': 1},
                {'task': 'b', 'passed': False, 'seconds': 4}]
        result = summarize_results(rows, ['a', 'b', 'c'], '0.03', pending='0.05')
        self.assertEqual(result['unattempted'], ['c'])
        self.assertEqual(result['success_rate_selected'], 1 / 3)
        self.assertEqual(result['success_rate_attempted'], .5)
        self.assertEqual(result['incorrect_edits'], {'count': 0, 'unknown_tasks': 2})
        self.assertFalse(result['cost_complete'])
        self.assertIsNone(result['cost_per_solved_usd'])
        self.assertEqual(result['tool_failures'], {'count': 1, 'unknown_tasks': 1})

    def test_empty_and_complete(self):
        self.assertIsNone(summarize_results([], [], '0')['median_task_seconds'])
        row = {'task': 'a', 'passed': True, 'seconds': 1}
        self.assertEqual(summarize_results([row], ['a'], '.02')['cost_per_solved_usd'], '0.02')
        with self.assertRaises(ValueError):
            summarize_results([row, row], ['a'], '0')
