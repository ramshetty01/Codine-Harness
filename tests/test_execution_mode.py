import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from codine.cli import main
from codine.repository_executor import RepositoryExecutor
from codine.restart import restart_process
from codine.sessions import Session, SessionStore
from codine.tools import build_registry, run_docker_command, execution_status
from codine.workspace import Workspace


class ExecutionModeTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)
        (self.root/'app.py').write_text('print("approved")')
        self.executor=Mock(image='sha256:'+'a'*64)
        self.executor.prepare_request.side_effect=RepositoryExecutor.prepare_request
        self.executor.execute.return_value={'status':'completed','exit_code':0}
        self.workspace=Workspace(self.root, lambda _:True)
        self.workspace.command_executor=self.executor

    def test_approval_uses_frozen_bytes_even_if_host_file_changes(self):
        approvals=[]
        def approve(text):
            approvals.append(text)
            (self.root/'app.py').write_text('print("unapproved")')
            return True
        self.workspace.approve=approve
        run_docker_command(self.workspace,['python','app.py'],['app.py'])
        snapshot=self.executor.execute.call_args.args[0]
        self.assertEqual(snapshot.files[0].content,'print("approved")')
        self.assertIn(snapshot.files[0].sha256,approvals[0])
        self.assertIn(snapshot.digest,approvals[0])
        self.assertIn(self.executor.image,approvals[0])

    def test_decline_and_invalid_cwd_never_execute(self):
        self.workspace.approve=Mock(return_value=False)
        result=run_docker_command(self.workspace,['python','app.py'],['app.py'])
        self.assertEqual(result['status'],'declined')
        self.executor.execute.assert_not_called()
        self.workspace.approve.reset_mock()
        with self.assertRaises(ValueError):
            run_docker_command(self.workspace,['python'],['app.py'],cwd='../')
        self.workspace.approve.assert_not_called()

    def test_schema_requires_files_and_plan_blocks_execution(self):
        registry=build_registry(self.workspace,lambda:None)
        self.assertIn('files',registry.tools['run_command'].parameters['required'])
        result=json.loads(registry.execute({'function':{'name':'run_command','arguments':'{"argv":["python"]}'}}))
        self.assertIn('error',result)
        self.workspace.mode='plan'
        self.assertNotIn('run_command',[item['function']['name'] for item in registry.schemas()])
        self.assertIn('DOCKER',execution_status(self.workspace))
        self.workspace.command_executor=None
        self.assertIn('outside a sandbox',execution_status(self.workspace))

    def test_cli_resume_retains_docker_and_unavailable_never_falls_back(self):
        with patch('codine.repository_executor.RepositoryExecutor',return_value=self.executor), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(['--project',str(self.root),'--executor','docker','--prompt','/sandbox']),0)
            store=SessionStore(self.root)
            ident=store.list()[0]['id']; session=store.load(ident); store.close()
            self.assertEqual(session.execution,{'executor':'docker','image':self.executor.image})
            self.assertEqual(main(['--project',str(self.root),'--resume',ident,'--prompt','/sandbox']),0)
        with patch('codine.repository_executor.RepositoryExecutor',side_effect=RuntimeError('Docker unavailable')), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(['--project',str(self.root),'--resume',ident,'--prompt','/sandbox']),1)
            self.assertIn('Docker unavailable',output.getvalue())

    def test_restart_passes_pinned_executor_and_image(self):
        session=Session.create(self.root,'offline','system')
        session.execution={'executor':'docker','image':self.executor.image}
        agent=SimpleNamespace(session=session,save=Mock(),store=Mock(),emit=Mock())
        received=[]
        with patch('codine.restart.subprocess.run',return_value=SimpleNamespace(returncode=0)):
            restart_process(agent,execute=lambda executable,argv:received.append(argv))
        argv=received[0]
        self.assertEqual(argv[argv.index('--executor')+1],'docker')
        self.assertEqual(argv[argv.index('--image')+1],self.executor.image)

    @unittest.skipUnless(os.getenv('CODINE_DOCKER_TESTS') == '1', 'Requires Docker opt-in')
    def test_registry_executes_approved_snapshot_in_real_docker(self):
        self.workspace.command_executor=RepositoryExecutor(timeout=5)
        registry=build_registry(self.workspace,lambda:None)
        result=json.loads(registry.execute({'function':{'name':'run_command',
            'arguments':json.dumps({'argv':['python','app.py'],'files':['app.py']})}}))
        self.assertEqual(result['exit_code'],0,result)
        self.assertEqual(result['stdout'],'approved\n')
        self.assertEqual(result['status'],'completed')

    def test_bootstrap_overrides_image_entrypoint_and_environment(self):
        executor=object.__new__(RepositoryExecutor)
        executor.image='sha256:'+'a'*64
        args=executor.create_args('test')
        self.assertEqual(args[args.index('--entrypoint')+1],'/usr/bin/env')
        image_index=args.index(executor.image)
        self.assertEqual(args[image_index+1:image_index+3],['-i','PATH=/usr/local/bin:/usr/bin:/bin'])
