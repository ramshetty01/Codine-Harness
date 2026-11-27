import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from codine.command_policy import CommandPolicy, default_policy_path
from codine.repository_executor import RepositoryExecutor
from codine.tools import run_command, run_docker_command
from codine.workspace import Workspace


class CommandPolicyTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name).resolve()
        self.path=self.root/'policy.json'
        self.base={'id':'tests','action':'allow','executor':'docker','project':str(self.root),
                   'cwd':'.','match':'exact','argv':['python','app.py']}
        (self.root/'app.py').write_text('print("test")')

    def load(self,*rules):
        self.path.write_text(json.dumps({'version':1,'rules':list(rules)}))
        self.path.chmod(0o600)
        return CommandPolicy.load(self.path)

    def decide(self,policy,**overrides):
        request={'executor':'docker','project':self.root,'cwd':'.','argv':['python','app.py'],'interactive':True}
        return policy.decide(**{**request,**overrides})

    def workspace(self,policy):
        workspace=Workspace(self.root,Mock(return_value=False),Mock())
        workspace.command_policy=policy; workspace.policy_interactive=True
        workspace.command_executor=Mock(image='sha256:'+'a'*64)
        workspace.command_executor.prepare_request.side_effect=RepositoryExecutor.prepare_request
        workspace.command_executor.execute.return_value={'status':'completed','exit_code':0}
        return workspace

    def test_scope_exact_matching_and_noninteractive(self):
        policy=self.load(self.base)
        self.assertEqual(self.decide(policy).action,'allow')
        for overrides in ({'executor':'host'},{'cwd':'tests'},{'project':self.root/'other'},
                          {'argv':['python','app.py','--extra']},{'interactive':False}):
            with self.subTest(overrides=overrides):
                self.assertEqual(self.decide(policy,**overrides).action,'ask')
        prefix=self.load({**self.base,'match':'prefix'})
        self.assertEqual(self.decide(prefix,argv=['python','app.py','--extra']).action,'allow')

    def test_deny_and_ask_override_allow(self):
        policy=self.load(self.base,{**self.base,'id':'manual','action':'ask'},
                         {**self.base,'id':'blocked','action':'deny'})
        self.assertEqual(self.decide(policy).action,'deny')
        self.assertIn('blocked',self.decide(policy).reason)
        policy=self.load(self.base,{**self.base,'id':'manual','action':'ask'})
        self.assertEqual(self.decide(policy).action,'ask')

    def test_denied_docker_and_host_commands_never_execute(self):
        workspace=self.workspace(self.load({**self.base,'action':'deny'}))
        result=run_docker_command(workspace,['python','app.py'],['app.py'])
        self.assertEqual(result['status'],'denied'); self.assertIn('tests',result['reason'])
        workspace.command_executor.execute.assert_not_called(); workspace.approve.assert_not_called()
        workspace.command_policy=self.load({**self.base,'action':'deny','executor':'host'})
        with patch('codine.tools.subprocess.Popen') as process:
            result=run_command(workspace,['python','app.py'])
            self.assertEqual(result['status'],'denied'); process.assert_not_called()

    def test_interactive_allow_executes_snapshot_noninteractive_requires_approval(self):
        workspace=self.workspace(self.load(self.base))
        run_docker_command(workspace,['python','app.py'],['app.py'])
        workspace.command_executor.execute.assert_called_once(); workspace.approve.assert_not_called()
        workspace.command_executor.execute.reset_mock(); workspace.policy_interactive=False
        result=run_docker_command(workspace,['python','app.py'],['app.py'])
        self.assertEqual(result['status'],'declined'); workspace.command_executor.execute.assert_not_called()
        workspace.approve.assert_called_once()
        workspace.mode='plan'
        with self.assertRaises(ValueError):
            run_docker_command(workspace,['python','app.py'],['app.py'])

    def test_invalid_and_shared_or_symlinked_policy_fail_closed(self):
        for changes in ({'cwd':'../bad'},{'executor':'any'},{'match':'glob'},
                        {'project':'.'},{'argv':[]},{'action':'execute'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.load({**self.base,**changes})
        with self.assertRaises(ValueError):
            self.load(self.base,self.base)
        self.path.write_text('{invalid')
        with self.assertRaises(ValueError): CommandPolicy.load(self.path)
        self.load(self.base); self.path.chmod(0o666)
        with self.assertRaises(ValueError): CommandPolicy.load(self.path)
        self.path.unlink(); self.path.symlink_to(self.root/'app.py')
        with self.assertRaises(ValueError): CommandPolicy.load(self.path)

    def test_no_project_policy_loading_and_config_protected_from_auto_edit(self):
        self.load(self.base)
        userhome=self.root/'user'; userhome.mkdir()
        with patch.dict(os.environ,{'CODINE_HOME':str(userhome)}):
            self.assertEqual(default_policy_path(),userhome/'command-policy.json')
            self.assertEqual(CommandPolicy.load().rules,())
        workspace=self.workspace(self.load(self.base)); workspace.mode='auto-edit'
        workspace.protected_paths={self.path}
        with self.assertRaisesRegex(ValueError,'protected'):
            workspace.read_file('policy.json')
        with self.assertRaisesRegex(ValueError,'protected'):
            workspace.write_file('policy.json','{}')

    def test_loaded_policy_remains_immutable_after_file_change(self):
        policy=self.load({**self.base,'action':'deny'})
        self.load(self.base)
        self.assertEqual(self.decide(policy).action,'deny')

    def test_project_dotenv_cannot_change_user_policy_discovery(self):
        from codine.config import Config
        (self.root/'.env').write_text(f'HOME={self.root}\nCODINE_HOME={self.root}\nXDG_CONFIG_HOME={self.root}\n')
        with patch.dict(os.environ,dict(os.environ),clear=True):
            os.environ.pop('HOME',None); os.environ.pop('CODINE_HOME',None); os.environ.pop('XDG_CONFIG_HOME',None)
            before=default_policy_path()
            Config.load(self.root,provider='nvidia')
            self.assertEqual(default_policy_path(),before)
            self.assertNotIn('HOME',os.environ)
            self.assertNotIn('CODINE_HOME',os.environ)
            self.assertNotIn('XDG_CONFIG_HOME',os.environ)

    def test_command_rules_do_not_autoapprove_core_file_edits(self):
        workspace=self.workspace(self.load(self.base))
        workspace.mode='auto-edit'; workspace.app_root=self.root
        (self.root/'main.py').write_text('old')
        workspace.read_file('main.py')
        result=workspace.edit_file('main.py','old','new')
        self.assertEqual(result['status'],'declined')
        workspace.approve.assert_called_once()
        self.assertEqual((self.root/'main.py').read_text(),'old')

    def test_json_cli_does_not_enable_policy_autoapproval(self):
        import contextlib
        import io
        from codine.cli import main
        from codine.tools import build_registry
        captured=[]
        def registry(workspace,restart):
            captured.append(workspace)
            return build_registry(workspace,restart)
        self.load(self.base)
        with patch('codine.cli.build_registry',side_effect=registry), contextlib.redirect_stdout(io.StringIO()):
            code=main(['--project',str(self.root),'--policy',str(self.path),'--json','--prompt','/policy'])
        self.assertEqual(code,0)
        self.assertFalse(captured[0].policy_interactive)
        self.assertEqual(self.decide(captured[0].command_policy,interactive=False).action,'ask')

    def test_explicit_policy_path_survives_restart_as_runtime_state(self):
        from types import SimpleNamespace
        from codine.restart import restart_process
        from codine.sessions import Session
        self.load(self.base)
        agent=SimpleNamespace(session=Session.create(self.root,'offline','system'),save=Mock(),
                              store=Mock(),emit=Mock(),command_policy_path=str(self.path))
        received=[]
        with patch('codine.restart.subprocess.run',return_value=SimpleNamespace(returncode=0)):
            restart_process(agent,execute=lambda executable,argv:received.append(argv))
        self.assertEqual(received[0][received[0].index('--policy')+1],str(self.path))
