import threading
import time
import unittest
from codine.config import Redactor
from codine.jobs import JobSupervisor


class JobSupervisorTests(unittest.TestCase):
    def test_counts_cancellation_and_closed_supervisor(self):
        supervisor=JobSupervisor(max_active=1,max_retained=2)
        self.addCleanup(supervisor.close)
        def run(cancel,output):
            cancel.wait(5)
            return {'status':'cancelled','exit_code':None}
        first=supervisor.start('test',run)
        with self.assertRaises(ValueError): supervisor.start('overflow',run)
        self.assertTrue(supervisor.cancel_job(first['id'])['cancel_requested'])
        supervisor.close()
        self.assertEqual(supervisor.status(first['id'])['status'],'cancelled')
        with self.assertRaises(ValueError): supervisor.start('closed',run)

    def test_logs_redacted_bounded_and_results_never_retained(self):
        supervisor=JobSupervisor(Redactor(['private-value']),log_limit=1024)
        def run(cancel,output):
            output({'stream':'stdout','text':'private-','final':False})
            output({'stream':'stdout','text':'value\x1b[31m'+'x'*2000,'final':True})
            return {'status':'completed','exit_code':0,'stdout':'private-value','credentials':{'secret':'private-value'}}
        job=supervisor.start('private-value',run)
        supervisor.close()
        result=supervisor.status(job['id'])
        self.assertNotIn('private-value',str(result))
        self.assertIn('[REDACTED]',result['logs'])
        self.assertNotIn('\x1b',result['logs'])
        self.assertLessEqual(len(result['logs']),1024)
        self.assertTrue(result['logs_truncated'])

    def test_failed_cleanup_reported_and_exception_text_withheld(self):
        supervisor=JobSupervisor()
        release=threading.Event()
        def run(cancel,output):
            release.wait(3)
            raise RuntimeError('unknown-secret-value')
        job=supervisor.start('failure',run)
        try:
            with self.assertRaisesRegex(RuntimeError,'cleanup'): supervisor.close(timeout=.01)
        finally:
            release.set()
            with self.assertRaisesRegex(RuntimeError,'cleanup'): supervisor.close()
        self.assertEqual(supervisor.status(job['id'])['status'],'failed')
        self.assertNotIn('unknown-secret-value',str(supervisor.status(job['id'])))

    def test_finished_history_pruned_and_restart_cleanup_keeps_supervisor_usable(self):
        supervisor=JobSupervisor(max_active=1,max_retained=2)
        def run(cancel,output): return {'status':'completed','exit_code':0}
        first=supervisor.start('first',run)
        supervisor.cancel_all()
        supervisor.start('second',run); supervisor.cancel_all()
        supervisor.start('third',run); supervisor.close()
        self.assertEqual(len(supervisor.status()),2)
        with self.assertRaisesRegex(ValueError,'expired'):
            supervisor.status(first['id'])

    def test_uncertain_cleanup_is_not_pruned_from_history(self):
        supervisor=JobSupervisor(max_active=1,max_retained=1)
        def fail(cancel,output): raise RuntimeError('cleanup unknown')
        job=supervisor.start('failed',fail)
        supervisor.jobs[job['id']].thread.join(1)
        with self.assertRaisesRegex(ValueError,'history'):
            supervisor.start('replacement',lambda cancel,output:{'status':'completed'})
        with self.assertRaisesRegex(RuntimeError,'cleanup'): supervisor.close()


class JobTransportTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        from codine.workspace import Workspace
        temporary=tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.workspace=Workspace(self.root,lambda _:True,lambda _:None)
        self.workspace.jobs=JobSupervisor()
        self.addCleanup(self.workspace.jobs.close)

    def wait(self,ident,status=None):
        limit=time.monotonic()+8
        while time.monotonic()<limit:
            result=self.workspace.jobs.status(ident)
            if (status and result['status']==status) or (not status and result['status'] not in ('queued','running')):
                return result
            time.sleep(.02)
        self.fail('Job did not reach expected state: '+str(result))

    def test_real_host_job_streams_then_cancels_and_releases_slot(self):
        from codine.tools import start_job
        job=start_job(self.workspace,['python','-c','import time; print("READY",flush=True); time.sleep(60)'])
        limit=time.monotonic()+3
        while 'READY' not in self.workspace.jobs.status(job['id'])['logs'] and time.monotonic()<limit:
            time.sleep(.02)
        self.assertIn('READY',self.workspace.jobs.status(job['id'])['logs'])
        self.workspace.jobs.cancel_job(job['id'])
        self.assertEqual(self.wait(job['id'])['status'],'cancelled')
        following=start_job(self.workspace,['python','-c','print("done")'])
        self.assertEqual(self.wait(following['id'])['exit_code'],0)

    def test_declined_or_plan_requests_never_create_jobs(self):
        from codine.tools import start_job
        self.workspace.approve=lambda _:False
        self.assertEqual(start_job(self.workspace,['python','-c','print(1)'])['status'],'declined')
        self.assertEqual(self.workspace.jobs.status(),[])
        self.workspace.mode='plan'
        with self.assertRaises(ValueError): start_job(self.workspace,['python','-c','print(1)'])

    def test_cleanup_cancels_real_host_job_before_returning(self):
        from codine.tools import start_job
        job=start_job(self.workspace,['python','-c','import time; time.sleep(60)'])
        self.workspace.jobs.close()
        self.assertEqual(self.workspace.jobs.status(job['id'])['status'],'cancelled')

    def test_restart_cancels_jobs_before_exec(self):
        from types import SimpleNamespace
        from unittest.mock import Mock,patch
        from codine.tools import start_job
        from codine.sessions import Session
        from codine.restart import restart_process
        job=start_job(self.workspace,['python','-c','import time; time.sleep(60)'])
        agent=SimpleNamespace(session=Session.create(self.root,'offline','system'),save=Mock(),store=Mock(),emit=Mock(),jobs=self.workspace.jobs)
        def execute(*args):
            self.assertEqual(self.workspace.jobs.status(job['id'])['status'],'cancelled')
        with patch('codine.restart.subprocess.run',return_value=SimpleNamespace(returncode=0)):
            restart_process(agent,execute=execute)

    def test_credential_job_preserves_main_thread_approval_and_withholds_logs(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from codine.repository_executor import RepositoryExecutor
        from codine.tools import start_job
        (self.root/'task.py').write_text('print("test")')
        secret='private-job-token'
        grant=SimpleNamespace(credentials=('TEST_JOB_TOKEN',),network='none',expands_access=True,
                              describe=lambda:'Named credentials supplied; output withheld')
        self.workspace.execution_profile=SimpleNamespace(grant=lambda **kwargs:grant)
        self.workspace.execution_credentials=SimpleNamespace(check_arguments=lambda grant:None,
                                                             for_grant=lambda grant:{'TEST_JOB_TOKEN':secret})
        self.workspace.policy_interactive=True
        main_thread=threading.get_ident()
        approvals=[]
        self.workspace.approve=lambda text: approvals.append(threading.get_ident()) or True
        executor=object.__new__(RepositoryExecutor); executor.image='sha256:'+'a'*64
        executor._run_payload=Mock(return_value={'status':'completed','exit_code':0,'stdout':secret,
                                                'stderr':'encoded-sensitive-output'})
        self.workspace.command_executor=executor
        job=start_job(self.workspace,['python','task.py'],files=['task.py'])
        result=self.wait(job['id'])
        self.assertEqual(approvals,[main_thread])
        self.assertTrue(result['output_withheld'])
        self.assertEqual(result['logs'],'')
        self.assertNotIn(secret,str(result))
        self.assertIsNone(executor._run_payload.call_args.kwargs['on_output'])


import os
@unittest.skipUnless(os.getenv('CODINE_DOCKER_TESTS')=='1','Requires Docker opt-in')
class JobDockerTests(unittest.TestCase):
    setUp = JobTransportTests.setUp
    wait = JobTransportTests.wait
    def test_real_docker_job_cancel_removes_container_and_keeps_snapshot(self):
        from unittest.mock import patch
        from codine.repository_executor import RepositoryExecutor
        from codine.sandbox import SandboxError
        from codine.tools import start_job
        (self.root/'task.py').write_text('import time\nprint("APPROVED",flush=True)\ntime.sleep(60)')
        executor=RepositoryExecutor(timeout=5)
        self.workspace.command_executor=executor
        names=[]; control=executor._control
        def tracking(args):
            if args[0]=='create': names.append(args[args.index('--name')+1])
            return control(args)
        def approve(text):
            (self.root/'task.py').write_text('print("CHANGED")')
            return True
        self.workspace.approve=approve
        with patch.object(executor,'_control',side_effect=tracking):
            job=start_job(self.workspace,['python','task.py'],files=['task.py'])
            limit=time.monotonic()+8
            while 'APPROVED' not in self.workspace.jobs.status(job['id'])['logs'] and time.monotonic()<limit:
                time.sleep(.02)
            self.assertIn('APPROVED',self.workspace.jobs.status(job['id'])['logs'])
            self.workspace.jobs.cancel_job(job['id'])
            self.assertEqual(self.wait(job['id'])['status'],'cancelled')
        with self.assertRaises(SandboxError): control(['inspect',names[0]])


class JobCLITests(unittest.TestCase):
    def test_prompt_remains_usable_and_exit_cleans_background_process(self):
        import os
        import sys
        import signal
        import tempfile
        from pathlib import Path
        from test_terminal_integration import PTY
        driver='''
import json
from codine import cli
class Fake:
 def __init__(self,config): pass
 def close(self): pass
 def complete(self,messages,schemas,model,emit):
  if messages[-1]['role']=='user':
   args={'argv':['python','-c','import os,pathlib,time; pathlib.Path("job.pid").write_text(str(os.getpid())); print("READY",flush=True); time.sleep(60)']}
   return {'role':'assistant','content':None,'tool_calls':[{'id':'job','type':'function','function':{'name':'start_job','arguments':json.dumps(args)}}]},'tool_calls',None
  emit('text','BACKGROUND_STARTED')
  return {'role':'assistant','content':'BACKGROUND_STARTED'},'stop',None
cli.NvidiaProvider=Fake
raise SystemExit(cli.main(['--provider','nvidia']))
'''
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            terminal=PTY([sys.executable,'-c',driver],root)
            try:
                terminal.until('›'.encode()); terminal.send(b'start background test\r')
                terminal.until(b'Approve? Type yes:'); terminal.send(b'yes\r')
                terminal.until(b'BACKGROUND_STARTED')
                terminal.send(b'/jobs\r'); terminal.until(b'"status": "running"')
                deadline=time.monotonic()+3
                while not (root/'job.pid').exists() and time.monotonic()<deadline: time.sleep(.02)
                pid=int((root/'job.pid').read_text())
                terminal.send(b'exit\r'); terminal.until(b'Goodbye!')
                self.assertEqual(terminal.process.wait(timeout=8),0)
                with self.assertRaises(ProcessLookupError): os.kill(pid,0)
            finally:
                terminal.close()
                if (root/'job.pid').exists():
                    try: os.kill(int((root/'job.pid').read_text()),signal.SIGKILL)
                    except ProcessLookupError: pass


class JobLifetimeTests(unittest.TestCase):
    def test_supervisor_enforces_lifetime_and_releases_prepared_closure(self):
        import gc
        import weakref
        class PrivateInputs: pass
        private=PrivateInputs(); reference=weakref.ref(private)
        def prepare(value):
            def run(cancel,output):
                self.assertIsNotNone(value)
                cancel.wait(5)
                return {'status':'cancelled','exit_code':None}
            return run
        supervisor=JobSupervisor(default_lifetime=1)
        runner=prepare(private)
        job=supervisor.start('bounded',runner)
        del runner,private
        supervisor.jobs[job['id']].thread.join(3)
        self.assertEqual(supervisor.status(job['id'])['status'],'timeout')
        supervisor.close(); gc.collect()
        self.assertIsNone(reference())

    def test_long_job_timeout_visible_but_foreground_limit_unchanged(self):
        import tempfile
        from pathlib import Path
        from codine.tools import start_job,run_command
        from codine.workspace import Workspace
        with tempfile.TemporaryDirectory() as directory:
            approvals=[]
            workspace=Workspace(Path(directory),lambda text:approvals.append(text) or True,lambda _:None)
            workspace.jobs=JobSupervisor()
            job=start_job(workspace,['python','-c','print("quick check")'],timeout=1800)
            workspace.jobs.jobs[job['id']].thread.join(3)
            workspace.jobs.close()
            self.assertEqual(workspace.jobs.status(job['id'])['timeout_seconds'],1800)
            self.assertIn('Timeout: 1800s',approvals[0])
            with self.assertRaises(ValueError): run_command(workspace,['python','-c','print(1)'],timeout=121)
            with self.assertRaises(ValueError): start_job(workspace,['python','-c','print(1)'],timeout=3601)
