import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from codine.config import Config
from codine.settings import describe


class SettingsTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name).resolve();self.project=self.root/'project';self.project.mkdir()
        self.user=self.root/'user.env';self.legacy=self.root/'legacy.env'
        self.addCleanup(patch.stopall)
        patch('codine.settings.USER_ENV',self.user).start()
        patch('codine.settings.LEGACY_ENV',self.legacy).start()
        patch.dict(os.environ,{},clear=True).start()

    def write(self,path,text):path.write_text(text)

    def test_precedence_provenance_and_no_process_environment_mutation(self):
        self.write(self.legacy,'NVIDIA_MODEL=legacy\nCODINE_MAX_TOKENS=100\n')
        self.write(self.user,'NVIDIA_MODEL=user\nCODINE_MAX_TOKENS=200\n')
        self.write(self.project/'.env','NVIDIA_MODEL=project\nCODINE_MAX_TOKENS=300\n')
        cfg=Config.load(self.project)
        self.assertEqual((cfg.model,cfg.max_tokens),('project',300))
        self.assertEqual(cfg.setting_sources['model'],'project .env')
        self.assertEqual(dict(os.environ),{})
        os.environ['NVIDIA_MODEL']='shell'
        os.environ['CODINE_MAX_TOKENS']='400'
        cfg=Config.load(self.project,model='cli')
        self.assertEqual((cfg.model,cfg.max_tokens),('cli',400))
        self.assertEqual(cfg.setting_sources['model'],'CLI')
        self.assertEqual(cfg.setting_sources['max_tokens'],'shell')
        del os.environ['NVIDIA_MODEL'];del os.environ['CODINE_MAX_TOKENS']
        (self.project/'.env').unlink()
        self.assertEqual(Config.load(self.project).model,'user')
        self.user.unlink()
        self.assertEqual(Config.load(self.project).setting_sources['model'],'legacy checkout .env')

    def test_project_settings_do_not_leak_to_later_project_or_trust_roots(self):
        text='NVIDIA_MODEL=first\nHOME=attacker\nCODINE_HOME=attacker\nXDG_CONFIG_HOME=attacker\nXDG_STATE_HOME=attacker\nHTTPS_PROXY=attacker\nOPENAI_BASE_URL=https://attacker.invalid\n'
        self.write(self.project/'.env',text)
        before=dict(os.environ);Config.load(self.project)
        self.assertEqual(dict(os.environ),before)
        second=self.root/'second';second.mkdir()
        self.assertNotEqual(Config.load(second).model,'first')

    def test_project_keys_supported_values_redacted_and_repr_private(self):
        self.write(self.user,'PASSWORD=global-private-secret\n')
        self.write(self.project/'.env','CODINE_PROVIDER=openrouter\nOPENROUTER_MODEL=vendor/model\nOPENROUTER_API_KEY=private-project-key\nDATABASE_PASSWORD=project-private-secret\nCODINE_MODEL_LIMITS={"private-project-key":{"context_tokens":8192}}\n')
        cfg=Config.load(self.project)
        self.assertEqual(cfg.api_key,'private-project-key')
        self.assertEqual(cfg.setting_sources['api_key'],'project .env')
        output=describe(cfg)
        self.assertNotIn('private-project-key',output)
        self.assertNotIn('private-project-key',repr(cfg))
        self.assertIn('api_key: value hidden [project .env]',output)
        self.assertEqual(cfg.redactor().text('global-private-secret project-private-secret'),'[REDACTED] [REDACTED]')
        self.assertNotIn('OPENROUTER_API_KEY',os.environ)

    def test_budget_project_only_tightens_and_existing_ledger_not_touched(self):
        self.write(self.user,'CODINE_BUDGET_USD=2\nCODINE_REQUEST_BUDGET_USD=.15\n')
        self.write(self.project/'.env','CODINE_BUDGET_USD=99\nCODINE_REQUEST_BUDGET_USD=1\n')
        cfg=Config.load(self.project)
        self.assertEqual((cfg.budget_usd,cfg.request_budget_usd),('2','0.15'))
        self.assertIn('cannot increase',cfg.setting_sources['budget_usd'])
        self.write(self.project/'.env','CODINE_BUDGET_USD=.5\nCODINE_REQUEST_BUDGET_USD=.05\n')
        cfg=Config.load(self.project)
        self.assertEqual((cfg.budget_usd,cfg.request_budget_usd),('0.5','0.05'))
        self.assertIn('tightens user',cfg.setting_sources['budget_usd'])
        self.assertIn('/budget shows active ledger limits',describe(cfg))
        self.assertFalse((self.project/'.codine').exists())

    def test_same_project_env_cannot_pose_as_trusted_legacy_budget(self):
        self.write(self.project/'.env','CODINE_BUDGET_USD=99\n')
        with patch('codine.settings.LEGACY_ENV',self.project/'.env'):
            self.assertEqual(Config.load(self.project).budget_usd,'3.50')

    def test_no_expansion_and_invalid_values_do_not_echo_secrets(self):
        os.environ['MY_SECRET']='private-shell-secret'
        self.write(self.project/'.env','NVIDIA_MODEL=${MY_SECRET}\n')
        self.assertEqual(Config.load(self.project).model,'${MY_SECRET}')
        for content in ('CODINE_MAX_TOKENS=private-shell-secret\n', 'CODINE_MODEL_LIMITS=private-shell-secret\n',
                        'CODINE_BUDGET_USD=NaN\n','CODINE_MAX_TOKENS=65536\nCODINE_CONTEXT_TOKENS=65536\n'):
            self.write(self.project/'.env',content)
            with self.assertRaises(ValueError) as error:Config.load(self.project)
            self.assertNotIn('private-shell-secret',str(error.exception))

    def test_restart_only_snapshot_and_settings_command_no_reads(self):
        from codine.cli import command
        self.write(self.project/'.env','NVIDIA_MODEL=before\n')
        cfg=Config.load(self.project)
        self.write(self.project/'.env','NVIDIA_MODEL=after\n')
        ui=Mock(); agent=Mock();agent.config=cfg
        with patch('codine.settings.read_env',side_effect=AssertionError('unexpected reload')):
            command('/settings',agent,Mock(),ui)
        self.assertIn('model: before [project .env]',ui.write.call_args.args[0])
        self.assertEqual(Config.load(self.project).model,'after')

    def test_public_token_counts_are_not_misclassified_as_secrets(self):
        os.environ['CODINE_CONTEXT_TOKENS']='1000000'
        self.write(self.project/'.env','CODINE_MAX_TOKENS=100000\n')
        output=describe(Config.load(self.project))
        self.assertIn('max_tokens: 100000',output)
        self.assertIn('context_tokens: 1000000',output)

    def test_symlink_and_oversized_env_are_rejected(self):
        self.user.write_text('NVIDIA_MODEL=user\n')
        (self.project/'.env').symlink_to(self.user)
        with self.assertRaises(ValueError):Config.load(self.project)
        (self.project/'.env').unlink();self.write(self.project/'.env','x'*65537)
        with self.assertRaises(ValueError):Config.load(self.project)


if __name__=='__main__':unittest.main()
