import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from codine.command_policy import CommandPolicy, FileRule, file_glob_matches
from codine.workspace import Workspace


class FilePolicyTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name).resolve()
        self.workspace=Workspace(self.root,Mock(return_value=True),Mock(),app_root=self.root,track_changes=True)
        self.workspace.policy_interactive=True
        (self.root/'allowed.txt').write_text('visible target')
        (self.root/'private.txt').write_text('secret target')

    def policy(self, *rules):
        built=[FileRule(id=str(index),action=action,operation=operation,project=str(self.root),match=match,path=path)
               for index,(action,operation,match,path) in enumerate(rules)]
        self.workspace.command_policy=CommandPolicy(self.root/'user-policy.json',file_rules=tuple(built))

    def test_deny_precedes_disk_read_write_and_read_results_are_filtered(self):
        self.policy(('deny','read','exact','private.txt'),('deny','write','exact','blocked.txt'))
        with patch.object(self.workspace,'_read') as read:
            with self.assertRaisesRegex(ValueError,'DENY'): self.workspace.read_file('private.txt')
            read.assert_not_called()
        with patch.object(self.workspace,'_atomic_write') as write:
            with self.assertRaisesRegex(ValueError,'DENY'): self.workspace.write_file('blocked.txt','new')
            write.assert_not_called()
        self.assertEqual(self.workspace.list_files()['files'],['allowed.txt'])
        found=self.workspace.search('target')
        self.assertEqual([row['path'] for row in found['matches']],['allowed.txt'])
        self.workspace.approve.assert_not_called()

    def test_write_allow_skips_ordinary_prompt_but_core_and_credentials_stay_protected(self):
        self.policy(('allow','write','glob','**'))
        self.workspace.write_file('ordinary.txt','new')
        self.workspace.approve.assert_not_called()
        self.workspace.approve.return_value=False
        result=self.workspace.write_file('main.py','print(1)')
        self.assertEqual(result['status'],'declined')
        self.workspace.approve.assert_called_once()
        self.assertIn('Core files',self.workspace.last_policy_decision.reason)
        for path in ('.env','.ssh/key.txt','outside.key','.git/config'):
            with self.subTest(path=path),self.assertRaises(ValueError): self.workspace.write_file(path,'new')
        self.assertFalse((self.root/'main.py').exists())

    def test_explicit_ask_overrides_autoedit_and_noninteractive_allow_does_not_bypass(self):
        self.policy(('ask','write','exact','new.txt'))
        self.workspace.mode='auto-edit'; self.workspace.approve.return_value=False
        self.assertEqual(self.workspace.write_file('new.txt','new')['status'],'declined')
        self.policy(('allow','write','exact','new.txt'))
        self.workspace.policy_interactive=False
        self.assertEqual(self.workspace.write_file('new.txt','new')['status'],'declined')
        self.assertFalse((self.root/'new.txt').exists())

    def test_read_ask_prompts_before_content_and_deny_wins_over_allow(self):
        self.policy(('ask','read','exact','private.txt'))
        self.workspace.approve.return_value=False
        with patch('codine.workspace._read_file') as reader:
            with self.assertRaisesRegex(ValueError,'declined'): self.workspace.read_file('private.txt')
            reader.assert_not_called()
        self.workspace.approve.return_value=True
        self.assertIn('secret target',self.workspace.read_file('private.txt')['content'])
        self.policy(('allow','read','glob','**'),('deny','read','exact','private.txt'))
        with self.assertRaisesRegex(ValueError,'DENY'): self.workspace.read_file('private.txt')

    def test_allow_does_not_relax_stale_guards_or_planning(self):
        self.policy(('allow','write','glob','**'))
        self.workspace.read_file('allowed.txt')
        (self.root/'allowed.txt').write_text('changed by another process')
        with self.assertRaisesRegex(ValueError,'current contents'):
            self.workspace.edit_file('allowed.txt','visible target','new')
        self.workspace.mode='plan'
        with self.assertRaisesRegex(ValueError,'planning'): self.workspace.write_file('new.txt','new')
        self.assertFalse((self.root/'new.txt').exists())

    def test_policy_and_linked_files_cannot_be_enabled_by_allow(self):
        self.policy(('allow','read','glob','**'),('allow','write','glob','**'))
        (self.root/'policy.json').write_text('{}')
        self.workspace.protected_paths={self.root/'policy.json'}
        with self.assertRaisesRegex(ValueError,'protected'): self.workspace.read_file('policy.json')
        (self.root/'linked.txt').symlink_to(self.root/'allowed.txt')
        with self.assertRaisesRegex(ValueError,'Symbolic'): self.workspace.read_file('linked.txt')

    def test_undo_respects_write_deny(self):
        self.workspace.write_file('created.txt','new')
        self.policy(('deny','write','exact','created.txt'))
        with self.assertRaisesRegex(ValueError,'DENY'): self.workspace.undo()
        self.assertEqual((self.root/'created.txt').read_text(),'new')

    def test_v1_compatibility_validation_and_explicit_glob_semantics(self):
        path=self.root/'rules.json'
        path.write_text(json.dumps({'version':1,'rules':[]})); path.chmod(0o600)
        self.assertEqual(CommandPolicy.load(path).file_rules,())
        rule={'id':'file','action':'deny','operation':'read','project':str(self.root),'match':'glob','path':'private/**'}
        path.write_text(json.dumps({'version':1,'rules':[],'file_rules':[rule]}))
        policy=CommandPolicy.load(path)
        self.assertIn('deny read',policy.describe())
        self.assertEqual(policy.decide_file(operation='read',project=self.root,path='private/a.txt',interactive=True).action,'deny')
        self.assertEqual(policy.decide_file(operation='read',project=self.root/'other',path='private/a.txt',interactive=True).action,'allow')
        for bad in ({'path':'../x'},{'path':'/x'},{'match':'prefix'},{'operation':'execute'}):
            path.write_text(json.dumps({'version':1,'rules':[],'file_rules':[{**rule,**bad}]}))
            with self.subTest(bad=bad),self.assertRaises(ValueError): CommandPolicy.load(path)
        self.assertTrue(file_glob_matches('a.py','**/*.py'))
        self.assertTrue(file_glob_matches('src/deep/a.py','src/**/*.py'))
        self.assertFalse(file_glob_matches('src/deep/a.py','src/*.py'))

    def test_docker_staging_cannot_bypass_read_deny(self):
        from codine.tools import prepare_docker_command
        self.policy(('deny','read','exact','private.txt'))
        self.workspace.command_executor=Mock(image='sha256:'+'a'*64)
        with patch('codine.staging.snapshot_repository') as snapshot:
            with self.assertRaisesRegex(ValueError,'DENY'):
                prepare_docker_command(self.workspace,['python','-V'],['private.txt'])
            snapshot.assert_not_called()
        self.workspace.approve.assert_not_called()

    def test_builtin_case_aliases_and_case_insensitive_policy_bypass_refused(self):
        self.policy(('allow','read','glob','**'),('deny','read','exact','private.txt'))
        for path in ('.ENV','.GIT/config','.SSH/key.txt'):
            with self.subTest(path=path),self.assertRaises(ValueError): self.workspace.resolve(path)
        if (self.root/'PRIVATE.txt').exists():
            with self.assertRaisesRegex(ValueError,'canonical'):
                self.workspace.read_file('PRIVATE.txt')
        self.assertTrue(file_glob_matches('caf\u00e9.txt','cafe\u0301.txt'))

    def test_protected_policy_identity_cannot_be_bypassed_without_file_rules(self):
        self.policy()
        (self.root/'policy.json').write_text('{}')
        self.workspace.protected_paths={self.root/'policy.json'}
        if (self.root/'POLICY.json').exists():
            with self.assertRaisesRegex(ValueError,'protected'): self.workspace.read_file('POLICY.json')
        import os
        os.link(self.root/'policy.json',self.root/'alias.json')
        with self.assertRaisesRegex(ValueError,'protected'): self.workspace.read_file('alias.json')

    def test_automatic_guidance_respects_denied_and_ask_reads(self):
        from codine.instructions import load_instructions
        (self.root/'AGENTS.md').write_text('private project guidance')
        for action in ('deny','ask'):
            self.policy((action,'read','exact','AGENTS.md'))
            guidance,paths=load_instructions(self.root,authorize=self.workspace.read_allowed_without_prompt)
            self.assertEqual((guidance,paths),('',[]))
            self.workspace.approve.assert_not_called()
        self.workspace.approve.return_value=True
        guidance,paths=load_instructions(self.root,authorize=lambda path:
            self.workspace._authorize_read(self.workspace.resolve(path.name)))
        self.assertIn('private project guidance',guidance)
        self.workspace.approve.assert_called_once()
