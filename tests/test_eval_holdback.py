"""Public checks guide work; held-back cases never enter model requests."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from codine.config import Config
from codine.evals import task_cases
from codine.live_evals import evaluate_task
from codine.paths import eval_resource


class HoldbackTests(unittest.TestCase):
    def task(self):
        return {'id': 'holdback-test', 'split': 'development', 'prompt': 'Implement solve.',
                'initial': 'def solve(value): return "public-answer"\n',
                'reference': '# reference-canary-never-in-request',
                'public_cases': [{'args': ['public-input'], 'expected': 'public-answer'}],
                'hidden_cases': [{'args': ['heldback-input-canary'], 'expected': 'heldback-answer-canary'}]}

    def test_all_shipped_fixtures_have_nonempty_disjoint_cases(self):
        for filename in ('tasks.json', 'repository_tasks.json'):
            data = json.loads(eval_resource(filename).read_text())
            self.assertEqual(data['version'], 2)
            for task in data['tasks']:
                with self.subTest(task=task['id']):
                    public, hidden = task_cases(task)
                    self.assertTrue(public and hidden)
                    self.assertNotIn('cases', task)

    def test_invalid_or_overlapping_cases_fail_closed(self):
        for mutate in (
            lambda task: task.update(public_cases=[]),
            lambda task: task.update(hidden_cases=[]),
            lambda task: task.update(hidden_cases=task['public_cases']),
            lambda task: task['public_cases'].append(copy.deepcopy(task['public_cases'][0])),
            lambda task: task.update(hidden_cases=[{'args': 'invalid', 'expected': 1}]),
            lambda task: task.update(hidden_cases=[{'args': []}]),
        ):
            task = self.task()
            mutate(task)
            with self.subTest(task=task), self.assertRaises(ValueError):
                task_cases(task)

    def test_public_pass_does_not_hide_final_failure_or_leak_heldback_data(self):
        task = self.task()
        requests, evaluations = [], []
        class Provider:
            calls = 0
            durations = []
            def complete(self, messages, schemas, model, emit):
                requests.append(copy.deepcopy([messages, schemas]))
                self.calls += 1
                if self.calls == 1:
                    reply = {'role': 'assistant', 'content': None, 'tool_calls': [
                        {'id': 'check', 'type': 'function', 'function': {'name': 'run_checks', 'arguments': '{}'}}]}
                    return reply, 'tool_calls', SimpleNamespace(cost=0)
                return {'role': 'assistant', 'content': 'done'}, 'stop', SimpleNamespace(cost=0)
        class Executor:
            def evaluate_files(self, files, entrypoint, args):
                evaluations.append(copy.deepcopy(args))
                # Candidate/reference/case data must not be materialized as extra files.
                self.files = files
                return {'status': 'completed', 'values': ['public-answer' for _ in args]}
        executor = Executor()
        with patch('codine.live_evals.OpenRouterProvider') as transport, patch('codine.live_evals.GuardedProvider', return_value=Provider()):
            result = evaluate_task(task, Config(Path.cwd(), provider='openrouter', api_key='offline'), executor, Mock(), Mock())
        self.assertEqual(len(requests), 2)
        self.assertEqual(evaluations[0], [['public-input']])
        self.assertEqual(evaluations[1], [['public-input'], ['heldback-input-canary']])
        self.assertFalse(result['passed'])
        self.assertEqual(result['outcome'], 'completed')
        tool_result = next(message for message in requests[1][0] if message['role'] == 'tool')
        self.assertTrue(json.loads(tool_result['content'])['passed'])
        serialized = json.dumps(requests)
        for secret in ('heldback-input-canary', 'heldback-answer-canary', 'reference-canary-never-in-request'):
            self.assertNotIn(secret, serialized)
            self.assertNotIn(secret, json.dumps(executor.files))
        self.assertEqual(set(executor.files), {'task.py'})
        transport.return_value.close.assert_called_once()

    def test_invalid_cases_rejected_before_provider_construction(self):
        task = self.task()
        task['hidden_cases'] = []
        with patch('codine.live_evals.OpenRouterProvider') as provider:
            with self.assertRaises(ValueError):
                evaluate_task(task, Config(Path.cwd()), Mock(), Mock(), Mock())
        provider.assert_not_called()
