"""Multi-module JavaScript fixture integrity and isolated execution acceptance."""
import copy
import json
import os
import unittest

from codine.command_evals import validate_pack, run_task
from codine.paths import eval_resource


class JavascriptCorpusTests(unittest.TestCase):
    def setUp(self):
        self.pack=json.loads(eval_resource('javascript_tasks.json').read_text())

    def test_pinned_multimodule_project_and_separate_oracles(self):
        validate_pack(self.pack)
        self.assertEqual(self.pack['provenance']['commit'],'d5d11c71a70bfb643a45d22821ed6d284240fce5')
        self.assertEqual({row['kind'] for row in self.pack['tasks']},
                         {'bugfix','feature','refactor','test-repair','recovery'})
        self.assertEqual({row['language'] for row in self.pack['tasks']},{'javascript'})
        files=self.pack['common_files']
        self.assertIn('MIT License',files['LICENSE.dot-prop.txt'])
        self.assertTrue({'package.json','src/settings.mjs','bin/config.mjs',
                         'fixtures/settings.json','tests/settings.test.mjs'} <= files.keys())
        for row in self.pack['tasks']:
            self.assertEqual(row['public_command'][0],'node')
            self.assertNotEqual(row['public_command'],row['final_command'])
            self.assertNotIn(row['final_command'][-1],files.values())

    def test_source_license_tampering_rejected_and_candidate_only_staging(self):
        for path in ('lib/dot-prop/index.js','LICENSE.dot-prop.txt'):
            pack=copy.deepcopy(self.pack);pack['common_files'][path]+='changed'
            with self.assertRaisesRegex(ValueError,'hash'):validate_pack(pack)
        calls=[]
        class Executor:
            def execute(self,manifest,argv,**kwargs):
                calls.append((manifest.as_text_files(),argv))
                return {'status':'completed','exit_code':1 if len(calls)<=2 else 0}
        task=self.pack['tasks'][0]
        self.assertTrue(run_task(self.pack,task,Executor())['passed'])
        for files,argv in calls:
            self.assertNotIn('javascript_tasks.json',files)
            self.assertNotIn(task['final_command'][-1],files.values())
            self.assertNotIn('reference_replacements',files)
        self.assertEqual(calls[0][1],task['public_command'])
        self.assertEqual(calls[1][1],task['final_command'])

    @unittest.skipUnless(os.getenv('CODINE_JS_TEST_IMAGE'),'Requires explicitly prepared Node/Python image')
    def test_real_node_baselines_fail_and_references_pass(self):
        from codine.repository_executor import RepositoryExecutor
        executor=RepositoryExecutor(os.environ['CODINE_JS_TEST_IMAGE'])
        for task in self.pack['tasks']:
            with self.subTest(task=task['id']):
                result=run_task(self.pack,task,executor)
                self.assertTrue(result['passed'],result)
                self.assertEqual(result['broken']['final']['status'],'completed',result)
                self.assertNotEqual(result['broken']['final']['exit_code'],0,result)
                self.assertTrue(all(row['status']=='completed' and row['exit_code']==0
                                    for row in result['reference'].values()),result)
