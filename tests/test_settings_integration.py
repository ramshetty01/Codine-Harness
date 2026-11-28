"""Fake-only configuration boundaries across resume, accounting and credentials."""
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock, patch

from codine.config import Config
from codine.sessions import Session, SessionStore


class SettingsIntegrationTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name).resolve()
        self.addCleanup(patch.stopall)
        patch.dict(os.environ,{},clear=True).start()
        patch('codine.settings.USER_ENV',self.root/'absent-user.env').start()
        patch('codine.settings.LEGACY_ENV',None).start()

    def saved(self):
        session=Session.create(self.root,'saved-nvidia','system',provider='nvidia')
        store=SessionStore(self.root);store.save(session);store.close()
        (self.root/'.env').write_text('CODINE_PROVIDER=openrouter\nOPENROUTER_API_KEY=fake-other-provider-key\nNVIDIA_API_KEY=fake-saved-provider-key\n')
        return session

    def test_resume_and_continue_use_saved_provider_before_default_validation(self):
        from codine.cli import main
        session=self.saved()
        for selection in (['--resume',session.id],['--continue']):
            output=io.StringIO()
            with patch('codine.cli.NvidiaProvider') as factory, patch('codine.cli.OpenRouterProvider',side_effect=AssertionError('wrong provider')), redirect_stdout(output):
                self.assertEqual(main(['--project',str(self.root),*selection,'--prompt','/settings']),0)
            cfg=factory.call_args.args[0]
            self.assertEqual((cfg.provider,cfg.model,cfg.api_key),('nvidia','saved-nvidia','fake-saved-provider-key'))
            factory.return_value.complete.assert_not_called()
            self.assertIn('provider: nvidia [saved session]',output.getvalue())
            self.assertIn('model: saved-nvidia [saved session]',output.getvalue())
            self.assertNotIn('fake-saved-provider-key',output.getvalue())
            self.assertNotIn('fake-other-provider-key',output.getvalue())

    def test_conflicting_provider_and_empty_continue_do_not_choose_fallback(self):
        from codine.cli import main
        session=self.saved()
        with patch('codine.cli.OpenRouterProvider') as factory,redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(['--project',str(self.root),'--resume',session.id,'--provider','openrouter','--prompt','/settings']),1)
        factory.assert_not_called()
        self.assertIn('Saved provider differs',output.getvalue())
        for path in (self.root/'.codine/sessions').glob('*.json'):path.unlink()
        with patch('codine.cli.NvidiaProvider') as factory,redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(['--project',str(self.root),'--continue','--prompt','/settings']),1)
        factory.assert_not_called()
        self.assertIn('Set OPENROUTER_MODEL explicitly',output.getvalue())

    def test_changed_saved_selection_fails_before_provider_creation(self):
        from codine.cli import main
        session=self.saved(); original=Config.load
        path=self.root/'.codine/sessions'/f'{session.id}.json'
        def changed(*args,**kwargs):
            cfg=original(*args,**kwargs)
            data=json.loads(path.read_text());data['model']='changed-during-startup'
            path.write_text(json.dumps(data))
            return cfg
        with patch('codine.cli.Config.load',side_effect=changed), patch('codine.cli.NvidiaProvider') as factory, redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(['--project',str(self.root),'--resume',session.id,'--prompt','/settings']),1)
        factory.assert_not_called()
        self.assertIn('changed during startup',output.getvalue())

    def test_loaded_key_reaches_only_explicit_accounting_and_content_is_redacted(self):
        from codine.agent import Agent
        from codine.registry import ToolRegistry
        from codine.terminal import Terminal
        (self.root/'.env').write_text('CODINE_PROVIDER=openrouter\nOPENROUTER_MODEL=fake-model\nOPENROUTER_API_KEY=fake-project-key\nDATABASE_PASSWORD=private-database-value\nCODINE_BUDGET_USD=.50\nCODINE_REQUEST_BUDGET_USD=.05\n')
        cfg=Config.load(self.root);redactor=cfg.redactor()
        self.assertNotIn('OPENROUTER_API_KEY',os.environ)
        session=Session.create(self.root,cfg.model,'system',provider=cfg.provider)
        store=SessionStore(self.root,redactor);self.addCleanup(store.close)
        class Fake:
            def complete(self,messages,schemas,model,emit):
                emit('text','private-database-');emit('text','value')
                return {'role':'assistant','content':'private-database-value'},'stop',SimpleNamespace(cost=.01,total_tokens=3)
        ui=Terminal(redactor,interactive=False)
        agent=Agent(cfg,Fake(),ToolRegistry(),store,session,ui.emit,redactor)
        metadata=Mock(return_value={'limit_remaining':5})
        agent.configure_shared_budget(self.root/'test-budget.json',metadata)
        with patch('codine.agent.load_instructions',return_value=('',[])),redirect_stdout(io.StringIO()) as output:
            self.assertEqual(agent.run_turn('hello'),'completed')
        metadata.assert_called_once_with('key','fake-project-key')
        self.assertNotIn('private-database-value',output.getvalue())
        self.assertEqual(session.messages[-1]['content'],'[REDACTED]')
        self.assertEqual(session.billing['limit'],'0.50')
        self.assertEqual(session.billing['spent'],'0.01')

    def test_project_credentials_do_not_enter_execution_shell_capture(self):
        from codine.execution_profiles import ExecutionProfile,ExecutionGrant
        (self.root/'.env').write_text('TEST_SERVICE_TOKEN=project-private-value\nHOME=spoofed\nXDG_CONFIG_HOME=spoofed\n')
        os.environ['TEST_SERVICE_TOKEN']='shell-private-value'
        profile=ExecutionProfile(self.root/'profile.json',str(self.root),'sha256:'+'a'*64,
            (ExecutionGrant('test','.',('python','-V'),'none',('TEST_SERVICE_TOKEN',)),))
        before=dict(os.environ)
        Config.load(self.root)
        self.assertEqual(dict(os.environ),before)
        self.assertEqual(profile.capture_shell().values,{'TEST_SERVICE_TOKEN':'shell-private-value'})


if __name__=='__main__':unittest.main()
