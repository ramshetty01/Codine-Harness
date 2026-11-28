import copy
import json
from pathlib import Path
import tempfile
import unittest
import uuid
from codine.eval_attempts import AttemptReport
from codine.compare_evals import compare_reports
from codine.eval_metrics import summarize_results
from codine.provenance import fingerprint
from tests.test_compare_evals import report

class AttemptTests(unittest.TestCase):
    def test_unique_attempts_existing_evidence_and_external_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'explicit.json';writer=AttemptReport(path)
            writer.save({'results':[]},status='aborted');saved=path.read_bytes()
            with self.assertRaises(FileExistsError):AttemptReport(path)
            self.assertEqual(path.read_bytes(),saved)
            first=AttemptReport(directory=directory);second=AttemptReport(directory=directory)
            self.assertNotEqual(first.id,second.id);self.assertNotEqual(first.path,second.path)
            path.write_text('external evidence')
            with self.assertRaises(ValueError):writer.save({'results':[]})
            self.assertEqual(path.read_text(),'external evidence')
    def test_copied_reports_cannot_fake_independent_repeats(self):
        same=report()
        with self.assertRaisesRegex(ValueError,'unique retained'):compare_reports([same,copy.deepcopy(same)],[report(),report()])
    def test_partial_aborted_attempt_stays_in_denominator(self):
        partial=report();partial['results']=[];partial['attempt']['status']='aborted';partial['cost_usd']=None
        result=compare_reports([report(),report()],[report(),partial])
        self.assertEqual(result['candidate']['mean_passed'],.5)
        self.assertEqual(result['task_changes'][0]['candidate_unattempted'],1)
        self.assertTrue(result['task_changes'][0]['regression'])
        self.assertEqual(result['candidate']['unknown_cost_runs'],1)
        self.assertEqual(result['candidate']['aborted_or_incomplete_runs'],1)
        self.assertEqual(result['candidate']['unknown_duration_runs'],1)
        self.assertIsNone(result['candidate']['runs'][1]['seconds'])
    def test_grader_or_request_condition_changes_rejected(self):
        for name in ('grading','request_settings','execution_settings'):
            changed=report();manifest=changed['provenance']
            manifest['controls']['values'][name]={'different':'condition'}
            manifest['controls']['sha256']=fingerprint(manifest['controls']['values'])
            manifest['sha256']=fingerprint({k:manifest[k] for k in ('source','tasks','configuration','runtime','controls')})
            with self.assertRaises(ValueError):compare_reports([report(),report()],[report(),changed])
    def test_unknown_and_partial_known_cost_not_zero_or_complete(self):
        row={'task':'a','passed':True,'seconds':1}
        result=summarize_results([row],['a'],None)
        self.assertIsNone(result['known_cost_usd']);self.assertFalse(result['cost_complete'])
        self.assertIsNone(result['cost_per_solved_usd'])
        pending=report();pending['pending_usd']='.05';pending['cost_usd']='.03'
        result=compare_reports([report(),report()],[report(),pending])
        self.assertEqual(result['candidate']['known_cost_usd'],'0.04')
        self.assertIsNone(result['candidate']['runs'][1]['metrics']['cost_per_solved_usd'])
    def test_offline_cli_retains_aborted_attempt_and_comparison_cli(self):
        import io
        from contextlib import redirect_stdout
        from unittest.mock import patch
        from codine.evals import main as evaluate
        from codine.compare_evals import main as compare
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);pack=root/'tasks.json'
            pack.write_text(json.dumps({'tasks':[{'id':'a','split':'development'},{'id':'b','split':'development'}]}))
            paths=[root/f'run{number}.json' for number in range(4)]
            with patch('codine.evals.eval_resource',return_value=pack),redirect_stdout(io.StringIO()):
                for path in paths[:3]:
                    with patch('codine.evals.run_case',side_effect=lambda task,executor:{'task':task['id'],'passed':True,'seconds':1}):
                        self.assertEqual(evaluate(['--offline','--report',str(path)]),0)
                with patch('codine.evals.run_case',side_effect=[{'task':'a','passed':False,'seconds':2},RuntimeError('fixture failure')]):
                    with self.assertRaises(RuntimeError):evaluate(['--offline','--report',str(paths[3])])
                combined=root/'comparison.json'
                self.assertEqual(compare(['--baseline',*map(str,paths[:2]),'--candidate',*map(str,paths[2:]),'--report',str(combined)]),0)
            failed=json.loads(paths[3].read_text());self.assertEqual(failed['attempt']['status'],'aborted')
            self.assertEqual(failed['metrics']['unattempted'],['b'])
            result=json.loads(combined.read_text());self.assertEqual(result['candidate']['aborted_or_incomplete_runs'],1)
            self.assertEqual(result['candidate']['mean_passed'],1)
            original=paths[0].read_bytes()
            with patch('codine.evals.eval_resource',return_value=pack):
                with self.assertRaises(FileExistsError):evaluate(['--offline','--report',str(paths[0])])
            self.assertEqual(paths[0].read_bytes(),original)
    def test_live_cli_fake_only_keeps_pending_cost_unknown_and_request_controls(self):
        import io
        from contextlib import redirect_stdout
        from types import SimpleNamespace
        from unittest.mock import Mock,patch
        from codine.config import Config
        from codine.live_evals import main
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);pack=root/'tasks.json';pack.write_text(json.dumps({'tasks':[{'id':'a'},{'id':'b'}]}))
            cfg=Config(root,provider='openrouter',model='fake-model',api_key='fake-only-key')
            shared=SimpleNamespace(ledger=SimpleNamespace(state={'spent':'0','pending':None,'reservation':None,'blocked':''}),close=Mock())
            catalog=[{'id':'fake-model','supported_parameters':['tools'],'pricing':{'prompt':'0','completion':'0'}}]
            observed=[]
            def evaluate(task,config,executor,guard,budget,profile):
                observed.append(copy.deepcopy(profile))
                budget.state['spent']='0.01';guard.ledger.state['spent']='0.01'
                if task['id']=='b':guard.ledger.state.update(pending='0.05',reservation='a'*32)
                return {'task':task['id'],'passed':task['id']=='a','seconds':1,'requests':1,'tool_failures':0,'error':'' if task['id']=='a' else 'provider failure','interventions':0}
            path=root/'live-fake.json'
            with patch('codine.live_evals.Config.load',return_value=cfg),patch('codine.live_evals.metadata',return_value=catalog),patch('codine.live_evals.eval_resource',return_value=pack),patch('codine.live_evals.DockerSandbox',return_value=SimpleNamespace(image='sha256:'+'a'*64,timeout=5,output_limit=65536)),patch('codine.live_evals.EvaluationBudget',return_value=shared),patch('codine.live_evals.evaluate_task',side_effect=evaluate),patch('codine.capabilities.declarations',return_value={}),patch('codine.capabilities.cached_record',return_value=None),redirect_stdout(io.StringIO()):
                self.assertEqual(main(['--model','fake-model','--report',str(path)]),1)
            saved=json.loads(path.read_text())
            self.assertEqual(saved['metrics']['known_cost_usd'],'0.01')
            self.assertFalse(saved['metrics']['cost_complete']);self.assertIsNone(saved['cost_per_solved_usd'])
            self.assertEqual(saved['provenance']['controls']['values']['request_settings']['max_tokens'],2048)
            self.assertEqual(observed,[{'declarations':{},'record':None}]*2)
            self.assertNotIn('fake-only-key',path.read_text());shared.close.assert_called_once()
