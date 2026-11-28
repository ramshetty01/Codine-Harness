"""Pinned second-repository task corpus checks; never constructs a model client."""
import copy
import hashlib
import json
import os
import unittest

from codine.command_evals import run_task, validate_pack
from codine.paths import eval_resource


class DotenvCorpusTests(unittest.TestCase):
    def setUp(self):
        self.pack=json.loads(eval_resource('dotenv_tasks.json').read_text())

    def test_pinned_source_license_and_all_task_categories(self):
        validate_pack(self.pack)
        self.assertEqual(self.pack['provenance']['commit'],'a565c2cc41599c48eabc6b7b7f5b826d43c5a6d7')
        self.assertEqual({row['kind'] for row in self.pack['tasks']},
                         {'bugfix','feature','refactor','test-repair','recovery'})
        self.assertEqual(len(self.pack['tasks']),5)
        self.assertEqual(hashlib.sha256(self.pack['common_files']['LICENSE.txt'].encode()).hexdigest(),
                         self.pack['provenance']['license_sha256'])
        self.assertIn('Saurabh Kumar',self.pack['common_files']['LICENSE.txt'])
        for task in self.pack['tasks']:
            self.assertIn('tests/test_parser.py',task['initial_overrides'])
            self.assertNotEqual(task['public_command'],task['final_command'])
            candidate={**self.pack['common_files'],**task['initial_overrides']}
            self.assertEqual(candidate['LICENSE.txt'],self.pack['common_files']['LICENSE.txt'])
            self.assertTrue(task['reference_replacements'])
            self.assertNotIn(task['final_command'][-1],candidate.values())

    def test_source_and_license_tampering_rejected(self):
        for path in ('dotenv/parser.py','LICENSE.txt'):
            pack=copy.deepcopy(self.pack);pack['common_files'][path]+='changed'
            with self.assertRaisesRegex(ValueError,'hash'):validate_pack(pack)

    def test_false_success_and_infrastructure_error_cannot_pass(self):
        class Executor:
            def execute(self,*args,**kwargs):return {'status':'completed','exit_code':0}
        self.assertFalse(run_task(self.pack,self.pack['tasks'][0],Executor())['passed'])
        class Executor:
            def execute(self,*args,**kwargs):return {'status':'timeout','exit_code':0}
        self.assertFalse(run_task(self.pack,self.pack['tasks'][0],Executor())['passed'])

    @unittest.skipUnless(os.getenv('CODINE_DOCKER_TESTS')=='1','Requires explicit local Docker opt-in')
    def test_all_baselines_rejected_and_reference_oracles_pass(self):
        from codine.repository_executor import RepositoryExecutor
        executor=RepositoryExecutor('python:3.12-slim')
        for task in self.pack['tasks']:
            with self.subTest(task=task['id']):
                result=run_task(self.pack,task,executor)
                self.assertTrue(result['passed'],result)
                self.assertEqual(result['broken']['final']['status'],'completed',result)
                self.assertNotEqual(result['broken']['final']['exit_code'],0,result)
                for row in result['reference'].values():
                    self.assertEqual((row['status'],row['exit_code']),('completed',0),result)
