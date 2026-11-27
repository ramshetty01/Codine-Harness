import copy
import json
import unittest

from codine.command_evals import run_task, validate_pack
from codine.paths import eval_resource


class CommandTaskTests(unittest.TestCase):
    def setUp(self):
        self.pack = json.loads(eval_resource('command_tasks.json').read_text())

    def test_pack_has_five_categories_pinned_source_and_license(self):
        validate_pack(self.pack)
        self.assertEqual({t['kind'] for t in self.pack['tasks']},
                         {'bugfix', 'feature', 'refactor', 'test-repair', 'recovery'})
        self.assertIn('Redistribution', self.pack['common_files']['LICENSE.txt'])
        self.assertIn('Copyright', self.pack['common_files']['boltons/mathutils.py'])

    def test_bad_paths_source_tampering_and_empty_final_checks_rejected(self):
        for mutate in (
            lambda p: p['common_files'].update({'../outside.py': 'bad'}),
            lambda p: p['common_files'].update({'boltons/mathutils.py': 'tampered'}),
            lambda p: p['tasks'][0].update(final_command=[]),
            lambda p: p['tasks'][0].update(final_command=p['tasks'][0]['public_command']),
            lambda p: p['provenance'].update(commit='main'),
        ):
            pack = copy.deepcopy(self.pack)
            mutate(pack)
            with self.assertRaises(ValueError):
                validate_pack(pack)

    def test_stages_only_candidate_files_and_separates_public_final_commands(self):
        calls = []
        class Executor:
            def execute(self, manifest, argv, **kwargs):
                calls.append((manifest.as_text_files(), argv))
                return {'status': 'completed', 'exit_code': 1 if len(calls) <= 2 else 0}
        task = self.pack['tasks'][0]
        result = run_task(self.pack, task, Executor())
        self.assertTrue(result['passed'])
        self.assertEqual(len(calls), 4)
        self.assertEqual(calls[0][1], task['public_command'])
        self.assertEqual(calls[1][1], task['final_command'])
        self.assertNotEqual(calls[0][0]['boltons/mathutils.py'], calls[2][0]['boltons/mathutils.py'])
        self.assertEqual(set(calls[0][0]), set(self.pack['common_files']))
        for files, _ in calls:
            self.assertNotIn(task['final_command'][2], files.values())
            self.assertNotIn('command_tasks.json', files)

    def test_vacuous_baseline_and_timeout_never_pass(self):
        class Executor:
            def execute(self, *args, **kwargs):
                return {'status': 'completed', 'exit_code': 0}
        self.assertFalse(run_task(self.pack, self.pack['tasks'][0], Executor())['passed'])
        class Timeout:
            def execute(self, *args, **kwargs):
                return {'status': 'timeout', 'exit_code': 0}
        self.assertFalse(run_task(self.pack, self.pack['tasks'][0], Timeout())['passed'])
