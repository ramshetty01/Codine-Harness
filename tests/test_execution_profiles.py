import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from codine.execution_profiles import ExecutionProfile, ShellCredentials

IMAGE = 'sha256:' + 'a' * 64


class ExecutionProfileTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.project = self.root / 'project'; self.project.mkdir()
        self.source = self.root / 'profile.json'
        self.rule = {'id': 'integration', 'cwd': '.', 'argv': ['python', 'check.py'],
                     'network': 'bridge', 'credentials': ['TEST_SERVICE_TOKEN']}
        self.document = {'version': 1, 'executor': 'docker', 'project': str(self.project),
                         'image': IMAGE, 'filesystem': 'staged-only', 'rules': [self.rule]}

    def load(self):
        self.source.write_text(json.dumps(self.document)); self.source.chmod(0o600)
        return ExecutionProfile.load(self.source)

    def test_exact_scope_and_no_model_chosen_grants(self):
        profile = self.load()
        grant = profile.grant(project=self.project, image=IMAGE, cwd='.', argv=self.rule['argv'])
        self.assertTrue(grant.expands_access)
        self.assertIn('general outbound', grant.describe())
        self.assertIn('withheld', grant.describe())
        self.assertIsNone(profile.grant(project=self.project, image=IMAGE, cwd='.', argv=['python', 'other.py']))
        self.assertIsNone(profile.grant(project=self.project, image=IMAGE, cwd='tests', argv=self.rule['argv']))
        for project, executor, image in ((self.root, 'docker', IMAGE), (self.project, 'host', IMAGE),
                                         (self.project, 'docker', 'sha256:'+'b'*64)):
            with self.assertRaisesRegex(ValueError, 'does not match'):
                profile.check_scope(project, executor, image)

    def test_only_named_startup_shell_values_are_captured(self):
        profile = self.load()
        with patch.dict(os.environ, {'TEST_SERVICE_TOKEN': 'fake-startup-credential', 'OTHER_SECRET': 'not-selected'}, clear=True):
            captured = profile.capture_shell()
            os.environ['TEST_SERVICE_TOKEN'] = 'different-project-dotenv-value'
            values = captured.for_grant(profile.rules[0])
        self.assertEqual(values, {'TEST_SERVICE_TOKEN': 'fake-startup-credential'})
        self.assertNotIn('fake-startup', repr(captured))
        with self.assertRaisesRegex(ValueError, 'Missing'):
            ShellCredentials({}).for_grant(profile.rules[0])

    def test_restart_does_not_promote_dotenv_values_into_shell_authority(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from codine.restart import restart_process
        from codine.sessions import Session
        captured = ShellCredentials({'TEST_SERVICE_TOKEN': None, 'OTHER_SERVICE_KEY': 'fake-original'})
        session = Session.create(self.project, 'offline', 'system')
        session.execution = {'executor': 'docker', 'image': IMAGE}
        agent = SimpleNamespace(session=session, save=Mock(), store=Mock(), emit=Mock(),
            execution_profile_path=str(self.source), execution_credentials=captured)
        received = []
        def execute(program, argv):
            received.append(argv)
            self.assertNotIn('TEST_SERVICE_TOKEN', os.environ)
            self.assertEqual(os.environ['OTHER_SERVICE_KEY'], 'fake-original')
        with patch.dict(os.environ, {'TEST_SERVICE_TOKEN': 'fake-dotenv', 'OTHER_SERVICE_KEY': 'fake-changed'}):
            with patch('codine.restart.subprocess.run', return_value=SimpleNamespace(returncode=0)):
                restart_process(agent, execute=execute)
            self.assertEqual(os.environ['TEST_SERVICE_TOKEN'], 'fake-dotenv')
            self.assertEqual(os.environ['OTHER_SERVICE_KEY'], 'fake-changed')
        self.assertIn('--execution-profile', received[0])
        self.assertNotIn('fake-original', str(received))

    def test_cli_capture_precedes_dotenv_and_resume_has_no_profile_authority(self):
        import contextlib
        import io
        from unittest.mock import Mock
        from codine.cli import main
        from codine.sessions import SessionStore
        from codine.workspace import Workspace
        self.load()
        (self.project / '.env').write_text('TEST_SERVICE_TOKEN=fake-project-value\n')
        captured = []
        def workspace(*args, **kwargs):
            result = Workspace(*args, **kwargs); captured.append(result)
            return result
        with patch.dict(os.environ, {}, clear=True), patch('codine.cli.Workspace', side_effect=workspace), \
             patch('codine.repository_executor.RepositoryExecutor', return_value=Mock(image=IMAGE)), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(['--provider', 'nvidia', '--project', str(self.project), '--executor', 'docker',
                '--execution-profile', str(self.source), '--prompt', '/sandbox']), 0)
            self.assertIsNone(captured[0].execution_credentials.values['TEST_SERVICE_TOKEN'])
            store = SessionStore(self.project)
            ident = store.list()[0]['id']; session = store.load(ident); store.close()
            self.assertEqual(session.execution, {'executor': 'docker', 'image': IMAGE})
            self.assertEqual(main(['--provider', 'nvidia', '--project', str(self.project), '--resume', ident,
                                  '--prompt', '/sandbox']), 0)
            self.assertIsNone(captured[1].execution_profile)
        self.assertNotIn('fake-project-value', output.getvalue())
        for path in (self.project / '.codine' / 'sessions').glob('*.json'):
            self.assertNotIn('fake-project-value', path.read_text())
            self.assertNotIn(str(self.source), path.read_text())

    def test_project_owned_profiles_and_permission_bypass_are_rejected(self):
        self.source = self.project / 'profile.json'
        with self.assertRaisesRegex(ValueError, 'outside'):
            self.load()
        self.source = self.root / 'profile.json'
        self.load(); self.source.chmod(0o666)
        with self.assertRaises(ValueError):
            ExecutionProfile.load(self.source)

    def test_linked_profile_and_symlink_parent_rejected(self):
        self.load()
        linked = self.root / 'linked.json'; linked.symlink_to(self.source)
        with self.assertRaises(ValueError):
            ExecutionProfile.load(linked)
        os.link(self.source, self.root / 'hardlink.json')
        with self.assertRaises(ValueError):
            ExecutionProfile.load(self.source)

    def test_no_host_network_mounts_or_runtime_env_expansion(self):
        for change in ({'network': 'host'}, {'credentials': ['PYTHONPATH']},
                       {'credentials': ['LD_PRELOAD']}, {'credentials': ['TEST_SERVICE_TOKEN', 'TEST_SERVICE_TOKEN']},
                       {'credentials': {'TEST_SERVICE_TOKEN': 'model-value'}}, {'mounts': ['/home']}):
            self.document['rules'] = [{**self.rule, **change}]
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.load()
        self.document['rules'] = [self.rule]
        self.document['filesystem'] = 'host'
        with self.assertRaises(ValueError):
            self.load()

    def test_exact_rule_duplicates_fail_closed(self):
        self.document['rules'] = [self.rule, {**self.rule, 'id': 'another'}]
        with self.assertRaises(ValueError):
            self.load()


class ExecutionProfileCommandTests(ExecutionProfileTests):
    def workspace(self):
        from unittest.mock import Mock
        from codine.workspace import Workspace
        from codine.repository_executor import RepositoryExecutor
        profile = self.load()
        (self.project / 'check.py').write_text('print("safe")')
        workspace = Workspace(self.project, Mock(return_value=True), Mock())
        workspace.policy_interactive = True
        workspace.execution_profile = profile
        workspace.execution_credentials = ShellCredentials({'TEST_SERVICE_TOKEN': 'fake-scoped-credential'})
        workspace.command_output = Mock()
        executor = object.__new__(RepositoryExecutor)
        executor.image = IMAGE
        executor._run_payload = Mock(return_value={'status': 'completed', 'exit_code': 0, 'seconds': 0.1,
            'stdout': 'fake-scoped-credential', 'stderr': 'encoded-secret-output', 'unexpected': 'secret'})
        workspace.command_executor = executor
        return workspace

    def policy(self, workspace, action):
        from codine.command_policy import CommandPolicy, Rule
        workspace.command_policy = CommandPolicy(self.root / 'policy.json', (
            Rule('tests', action, 'docker', str(self.project), '.', 'exact', tuple(self.rule['argv'])),))

    def test_policy_allow_does_not_bypass_expansion_approval(self):
        from codine.tools import run_docker_command
        workspace = self.workspace(); self.policy(workspace, 'allow')
        result = run_docker_command(workspace, self.rule['argv'], ['check.py'])
        workspace.approve.assert_called_once()
        description = workspace.approve.call_args.args[0]
        self.assertIn('TEST_SERVICE_TOKEN', description)
        self.assertIn('Snapshot SHA256', description)
        self.assertIn(IMAGE, description)
        self.assertNotIn('fake-scoped-credential', description)
        self.assertTrue(result['output_withheld'])
        self.assertNotIn('fake-scoped-credential', json.dumps(result))
        self.assertNotIn('encoded-secret-output', json.dumps(result))
        self.assertNotIn('unexpected', result)
        self.assertIsNone(workspace.command_executor._run_payload.call_args.kwargs['on_output'])
        workspace.command_output.assert_not_called()

    def test_deny_decline_and_noninteractive_never_execute(self):
        from codine.tools import run_docker_command
        for mode in ('deny', 'decline', 'noninteractive'):
            workspace = self.workspace()
            self.policy(workspace, 'deny' if mode == 'deny' else 'allow')
            if mode == 'decline':
                workspace.approve.return_value = False
            if mode == 'noninteractive':
                workspace.policy_interactive = False
            result = run_docker_command(workspace, self.rule['argv'], ['check.py'])
            self.assertIn(result['status'], ('denied', 'declined'))
            workspace.command_executor._run_payload.assert_not_called()

    def test_network_and_secret_payload_are_scoped_to_one_transport(self):
        import copy
        from codine.repository_executor import RepositoryExecutor
        from codine.tools import run_docker_command
        workspace = self.workspace()
        del workspace.command_executor._run_payload
        observed = []
        def execute(transport, payload, **kwargs):
            observed.append((transport.create_args('scoped'), copy.deepcopy(payload), kwargs['on_output']))
            return {'status': 'completed', 'exit_code': 0, 'stdout': 'ok', 'stderr': ''}
        with patch.object(RepositoryExecutor, '_run_payload', autospec=True, side_effect=execute):
            run_docker_command(workspace, self.rule['argv'], ['check.py'])
            run_docker_command(workspace, ['python', '-V'], ['check.py'])
        first_args, first_payload, first_callback = observed[0]
        second_args, second_payload, _ = observed[1]
        self.assertEqual(first_args[first_args.index('--network') + 1], 'bridge')
        self.assertEqual(second_args[second_args.index('--network') + 1], 'none')
        self.assertNotIn('fake-scoped-credential', str(first_args))
        self.assertEqual(first_payload['env'], {'TEST_SERVICE_TOKEN': 'fake-scoped-credential'})
        self.assertEqual(second_payload['env'], {})
        self.assertIsNone(first_callback)

    def test_errors_are_sanitized_and_network_state_does_not_persist(self):
        from codine.tools import run_docker_command
        workspace = self.workspace()
        workspace.command_executor._run_payload.side_effect = RuntimeError('fake-scoped-credential encoded-secret-output')
        with self.assertRaisesRegex(ValueError, 'details withheld') as caught:
            run_docker_command(workspace, self.rule['argv'], ['check.py'])
        self.assertNotIn('fake-scoped-credential', str(caught.exception))
        self.assertEqual(getattr(workspace.command_executor, '_command_network', 'none'), 'none')
        args = workspace.command_executor.create_args('test')
        self.assertEqual(args[args.index('--network') + 1], 'none')
        self.assertNotIn('fake-scoped-credential', str(args))

    def test_unmatched_command_stays_offline_without_credentials(self):
        from codine.tools import run_docker_command
        workspace = self.workspace()
        workspace.command_executor._run_payload.return_value = {'status': 'completed', 'stdout': 'ordinary output'}
        result = run_docker_command(workspace, ['python', '-V'], ['check.py'])
        self.assertEqual(result['stdout'], 'ordinary output')
        payload = workspace.command_executor._run_payload.call_args.args[0]
        self.assertEqual(payload['env'], {})
        self.assertIs(workspace.command_executor._run_payload.call_args.kwargs['on_output'], workspace.command_output)

    def test_timed_out_partial_output_is_also_withheld(self):
        from codine.tools import run_docker_command
        workspace = self.workspace()
        workspace.command_executor._run_payload.return_value = {'status': 'timeout', 'exit_code': -9,
            'stdout': 'fake-scoped-credential', 'stderr': 'base64-output'}
        result = run_docker_command(workspace, self.rule['argv'], ['check.py'])
        self.assertEqual(result['status'], 'timeout')
        self.assertEqual(result['stdout'], '')
        self.assertEqual(result['stderr'], '')

    def test_missing_startup_credential_does_not_use_new_environment(self):
        from codine.tools import run_docker_command
        workspace = self.workspace()
        workspace.execution_credentials = ShellCredentials({})
        with patch.dict(os.environ, {'TEST_SERVICE_TOKEN': 'late-project-value'}):
            with self.assertRaisesRegex(ValueError, 'startup-shell'):
                run_docker_command(workspace, self.rule['argv'], ['check.py'])
        workspace.command_executor._run_payload.assert_not_called()


@unittest.skipUnless(os.getenv('CODINE_DOCKER_TESTS') == '1', 'Requires Docker opt-in')
class ProfileDockerTests(unittest.TestCase):
    def test_fake_credential_delivered_without_network_mounts_or_output_leakage(self):
        from codine.repository_executor import RepositoryExecutor
        from codine.staging import snapshot_repository
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'check.py').write_text('''import os, socket, pathlib, base64, sys
assert os.environ['TEST_SERVICE_TOKEN'] == 'fake-docker-credential'
assert 'UNSELECTED_SECRET' not in os.environ
assert os.getuid() == 65534
assert not pathlib.Path('/Users/ramshetty').exists()
try:
    socket.create_connection(('1.1.1.1', 443), timeout=0.5)
except OSError:
    pass
else:
    raise AssertionError('Unexpected network access')
print(os.environ['TEST_SERVICE_TOKEN'])
print(base64.b64encode(os.environ['TEST_SERVICE_TOKEN'].encode()).decode(), file=sys.stderr)
''')
            manifest = snapshot_repository(root, ['check.py'])
            executor = RepositoryExecutor(timeout=5)
            output = Mock()
            with patch.dict(os.environ, {'UNSELECTED_SECRET': 'not-forwarded'}):
                result = executor.execute(manifest, ['python', 'check.py'],
                    credentials={'TEST_SERVICE_TOKEN': 'fake-docker-credential'}, on_output=output)
            self.assertEqual(result['status'], 'completed')
            self.assertEqual(result['exit_code'], 0)
            self.assertTrue(result['output_withheld'])
            self.assertEqual((result['stdout'], result['stderr']), ('', ''))
            output.assert_not_called()
