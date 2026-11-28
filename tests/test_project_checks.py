import json
import os
from pathlib import Path
import shlex
import shutil
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from codine.project_checks import ProjectChecks
from codine.workspace import Workspace


class ProjectCheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.ws = Workspace(self.root, Mock(return_value=True), Mock())
        self.checks = ProjectChecks(self.ws)

    def test_multilanguage_discovery_never_executes(self):
        (self.root / 'pyproject.toml').write_text('[tool.pytest.ini_options]\n[tool.ruff]\n[tool.mypy]\n')
        (self.root / 'package.json').write_text(json.dumps({'scripts': {
            'test': 'touch should-not-exist', 'lint:ci': 'eslint .', 'typecheck': 'tsc',
            'deploy': 'arbitrary', 'test;evil': 'arbitrary'}}))
        (self.root / 'Cargo.toml').write_text('[package]\nname="demo"\n')
        (self.root / 'go.mod').write_text('module example.org/demo\n')
        with patch('subprocess.Popen', side_effect=AssertionError('No discovery execution')):
            rows = self.checks.discover()['checks']
        self.assertEqual(len(rows), 10)
        self.assertIn(['npm', 'run', 'test'], [row['argv'] for row in rows])
        self.assertFalse((self.root / 'should-not-exist').exists())
        self.ws.approve.assert_not_called()

    @unittest.skipUnless(shutil.which('make'), 'make unavailable')
    def test_real_pass_fail_and_decline_are_distinct(self):
        config = self.root / 'Makefile'
        config.write_text('test:\n\t' + shlex.quote(sys.executable) + ' -c "assert 2 + 2 == 4"\n')
        ident = self.checks.discover()['checks'][0]['id']
        result = self.checks.run(ident)
        self.assertEqual(result['classification'], 'passed')
        self.assertEqual(result['outcome']['exit_code'], 0)
        config.write_text('test:\n\t' + shlex.quote(sys.executable) + ' -c "assert False"\n')
        ident = self.checks.discover()['checks'][0]['id']
        result = self.checks.run(ident)
        self.assertEqual(result['classification'], 'failed')
        self.ws.approve.return_value = False
        result = self.checks.run(ident)
        self.assertEqual(result['classification'], 'not_verified')
        self.assertEqual(result['outcome']['status'], 'declined')
        self.assertEqual(len(self.checks.results), 3)

    def test_stale_before_and_during_approval(self):
        config = self.root / 'Makefile'
        config.write_text('test:\n\ttrue\n')
        ident = self.checks.discover()['checks'][0]['id']
        config.write_text('test:\n\tfalse\n')
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.checks.run(ident)
        ident = self.checks.discover()['checks'][0]['id']
        def approve(_):
            config.write_text('test:\n\techo replaced\n')
            return True
        self.ws.approve.side_effect = approve
        with patch('codine.tools._run_host') as dispatch:
            with self.assertRaisesRegex(ValueError, 'changed'):
                self.checks.run(ident)
            dispatch.assert_not_called()

    def test_scope_malformed_and_permission_filter(self):
        sub = self.root / 'sub'
        sub.mkdir()
        (sub / 'pytest.ini').write_text('')
        (sub / 'package.json').write_text('invalid JSON')
        rows = self.checks.discover('sub')
        self.assertEqual(rows['checks'][0]['cwd'], 'sub')
        self.assertEqual(len(rows['warnings']), 1)
        from codine.command_policy import CommandPolicy, FileRule
        self.ws.command_policy = CommandPolicy(self.root / 'policy.json', file_rules=(
            FileRule(id='private', action='ask', operation='read', project=str(self.root), match='glob', path='sub/**'),))
        self.assertEqual(self.checks.discover('sub')['checks'], [])
        self.ws.approve.assert_not_called()
        with self.assertRaises(ValueError):
            self.checks.discover('../')

    def test_plan_and_docker_missing_source_fail_before_dispatch(self):
        (self.root / 'pytest.ini').write_text('')
        ident = self.checks.discover()['checks'][0]['id']
        self.ws.mode = 'plan'
        with self.assertRaisesRegex(ValueError, 'planning'):
            self.checks.run(ident)
        self.ws.mode = 'ask'
        self.ws.command_executor = object()
        with self.assertRaisesRegex(ValueError, 'including'):
            self.checks.run(ident, files=['test_app.py'])
        self.ws.approve.assert_not_called()

    def test_timeout_zero_exit_never_means_passed(self):
        (self.root / 'pytest.ini').write_text('')
        ident = self.checks.discover()['checks'][0]['id']
        with patch('codine.tools.prepare_host_command', return_value=lambda *_: {'status': 'timeout', 'exit_code': 0}):
            result = self.checks.run(ident)
        self.assertEqual(result['classification'], 'not_verified')


    def test_docker_uses_discovered_argv_and_explicit_snapshot(self):
        (self.root / 'pytest.ini').write_text('')
        ident = self.checks.discover()['checks'][0]['id']
        self.ws.command_executor = object()
        def run(*_):
            return {'status': 'completed', 'exit_code': 7, 'manifest_digest': 'fake-manifest'}
        run.snapshot_hashes = {'pytest.ini': self.checks.candidates[ident]['source_sha256']}
        with patch('codine.tools.prepare_docker_command', return_value=run) as prepare:
            result = self.checks.run(ident, files=['pytest.ini', 'test_app.py'])
        prepare.assert_called_once_with(self.ws, ['python', '-m', 'pytest'],
                                       ['pytest.ini', 'test_app.py'], '.', 30)
        self.assertEqual(result['classification'], 'failed')
        self.assertEqual(result['outcome']['manifest_digest'], 'fake-manifest')

    def test_docker_rejects_changed_config_in_approved_snapshot(self):
        (self.root / 'pytest.ini').write_text('')
        ident = self.checks.discover()['checks'][0]['id']
        self.ws.command_executor = object()
        run = Mock()
        run.snapshot_hashes = {'pytest.ini': 'changed-during-staging'}
        with patch('codine.tools.prepare_docker_command', return_value=run):
            with self.assertRaisesRegex(ValueError, 'snapshot configuration'):
                self.checks.run(ident, files=['pytest.ini'])
        run.assert_not_called()

    def test_missing_runtime_is_recorded_as_not_verified(self):
        (self.root / 'pytest.ini').write_text('')
        ident = self.checks.discover()['checks'][0]['id']
        with patch('codine.tools.subprocess.Popen', side_effect=FileNotFoundError('private executable path')):
            result = self.checks.run(ident)
        self.assertEqual(result['classification'], 'not_verified')
        self.assertEqual(result['outcome']['status'], 'execution_error')
        self.assertIsNone(result['outcome']['exit_code'])
        self.assertEqual(self.checks.results[-1]['classification'], 'not_verified')
        self.assertNotIn('private executable path', json.dumps(result))

    def test_deeply_nested_json_is_reported_as_unavailable(self):
        (self.root / 'package.json').write_text('[' * 2000 + '0' + ']' * 2000)
        result = self.checks.discover()
        self.assertEqual(result['checks'], [])
        self.assertEqual(len(result['warnings']), 1)
        self.ws.approve.assert_not_called()
