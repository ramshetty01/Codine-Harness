import copy
import unittest
import uuid
from codine.compare_evals import compare_reports
from codine.provenance import evaluation_manifest, fingerprint


def report(passed=True):
    return {'attempt':{'id':uuid.uuid4().hex,'status':'completed'},'provenance': evaluation_manifest([{'id': 'a'}], mode='offline-scripted'),
            'results': [{'task': 'a', 'passed': passed, 'seconds': 1}], 'cost_usd': '0.01'}


class ComparisonTests(unittest.TestCase):
    def test_variance_and_regression(self):
        result = compare_reports([report(), report()], [report(), report(False)])
        self.assertEqual(result['task_changes'][0]['delta'], -0.5)
        self.assertGreater(result['candidate']['stdev_passed'], 0)
        self.assertEqual(result['baseline']['known_cost_usd'], '0.02')

    def test_controls_integrity_and_complete_runs(self):
        for mutation in ('model', 'duplicate_task', 'tampered', 'short'):
            with self.subTest(mutation=mutation):
                rows = [report(), report()]
                if mutation == 'model':
                    manifest = rows[0]['provenance']
                    manifest['configuration']['values']['model'] = 'different'
                    manifest['sha256'] = fingerprint({k: manifest[k] for k in ('source','tasks','configuration','runtime','controls')})
                elif mutation == 'duplicate_task':
                    rows[0]['results'] *= 2
                elif mutation == 'tampered':
                    rows[0]['provenance']['sha256'] = 'bad'
                else:
                    rows.pop()
                with self.assertRaises(ValueError):
                    compare_reports([report(), report()], rows)

    def test_unknown_cost_remains_unknown(self):
        row = report()
        row['pending_usd'] = '0.05'
        other=copy.deepcopy(row);other['attempt']['id']=uuid.uuid4().hex
        result = compare_reports([report(), report()], [row, other])
        self.assertEqual(result['candidate']['unknown_cost_runs'], 2)
        self.assertIsNone(result['candidate']['runs'][0]['cost_usd'])
