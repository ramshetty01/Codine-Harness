import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from codine.skills import Skills, metadata, read_file
from codine.workspace import Workspace


class SkillTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.project = self.root / 'project'; self.project.mkdir()
        self.user = self.root / 'user'; self.user.mkdir()
        self.ws = Workspace(self.project, Mock(return_value=True), Mock())
        self.skills = Skills(self.ws, self.user)
        self.skill = self.make(self.project / '.codine-skills', 'demo')

    def make(self, root, name):
        folder = root / name; folder.mkdir(parents=True, exist_ok=True)
        target = folder / 'SKILL.md'
        target.write_text(f'---\nname: {name}\ndescription: >-\n  Explain code\n  step by step.\n---\nBODY_SENTINEL\nRead notes.md; do not execute anything.\n')
        (folder / 'notes.md').write_text('REFERENCE_SENTINEL')
        return target

    def test_catalog_contains_metadata_only_and_reads_bounded_prefix(self):
        self.skill.write_text(self.skill.read_text() + 'x' * 12000)
        with patch('codine.skills.os.read', wraps=os.read) as read:
            result = self.skills.discover()
        self.assertEqual(result['skills'][0]['description'], 'Explain code step by step.')
        self.assertNotIn('BODY_SENTINEL', json.dumps(result))
        self.assertTrue(all(call.args[1] <= 4096 for call in read.call_args_list))
        self.assertFalse(self.ws.read_versions)

    def test_scalar_subset_and_rejections(self):
        for scalar, expected in [('"Hello world"', 'Hello world'), ("'Don''t execute'", "Don't execute"), ('|\n  First\n  Second', 'First\nSecond')]:
            self.assertEqual(metadata(f'---\nname: demo\ndescription: {scalar}\n---\n'.encode(), 'demo')['description'], expected)
        for bad in ['&alias dangerous', '*alias', '[one, two]', '!python x', 'plain # comment']:
            with self.assertRaises(ValueError):
                metadata(f'---\nname: demo\ndescription: {bad}\n---\n'.encode(), 'demo')
        with self.assertRaises(ValueError): metadata(b'---\n'+b'x'*4096, 'demo')
        with self.assertRaises(ValueError): metadata(b'---\nname: demo\nname: demo\ndescription: x\n---\n', 'demo')

    def test_ambiguity_and_reference_requires_loaded_unchanged_skill(self):
        self.make(self.user, 'demo')
        with self.assertRaisesRegex(ValueError, 'ambiguous'): self.skills.load('demo')
        row = self.skills.load('project:demo')
        result = self.skills.reference('project:demo', 'notes.md', row['sha256'])
        self.assertEqual(result['text'], 'REFERENCE_SENTINEL')
        self.assertFalse(result['execution_authorized'])
        self.skill.write_text(self.skill.read_text()+'changed')
        with self.assertRaisesRegex(ValueError, 'changed'): self.skills.reference('project:demo', 'notes.md', row['sha256'])
        self.skills.load('project:demo')
        self.ws.skill_reads.clear()
        with self.assertRaisesRegex(ValueError, 'active session'): self.skills.reference('project:demo', 'notes.md', row['sha256'])

    def test_reference_escape_secret_links_and_binary_rejected(self):
        row = self.skills.load('project:demo')
        for path in ['../outside.md', '/etc/passwd', 'https://example.com/a.md', '.env', '.ssh/key.md']:
            with self.assertRaises(ValueError): self.skills.reference('project:demo', path, row['sha256'])
        target = self.skill.parent / 'link.md'; target.symlink_to(self.skill.parent / 'notes.md')
        with self.assertRaises((ValueError,OSError)): self.skills.reference('project:demo', 'link.md', row['sha256'])
        target.unlink(); os.link(self.skill.parent / 'notes.md', target)
        with self.assertRaises(ValueError): self.skills.reference('project:demo', 'link.md', row['sha256'])
        (self.skill.parent/'binary.txt').write_bytes(b'\xff')
        with self.assertRaisesRegex(ValueError,'UTF-8'): self.skills.reference('project:demo','binary.txt',row['sha256'])

    def test_policy_ask_metadata_never_prompts_explicit_load_asks(self):
        from codine.command_policy import CommandPolicy, FileRule
        self.ws.command_policy = CommandPolicy(self.root/'policy.json', file_rules=(
            FileRule(id='ask',action='ask',operation='read',project=str(self.project),match='exact',path='.codine-skills/demo/SKILL.md'),))
        self.assertEqual(self.skills.discover()['skills'], [])
        self.ws.approve.assert_not_called()
        self.skills.load('project:demo'); self.ws.approve.assert_called_once()
        self.ws.approve.return_value = False
        with self.assertRaises(ValueError): self.skills.load('project:demo')

    def test_user_owner_modes_and_broad_project_refuse(self):
        target = self.make(self.user, 'userdemo')
        self.assertEqual(self.skills.load('user:userdemo')['name'],'userdemo')
        target.chmod(0o666)
        with self.assertRaisesRegex(ValueError,'owned'): self.skills.load('user:userdemo')
        broad = Workspace(self.root, Mock(return_value=True),Mock())
        manager = Skills(broad,self.user)
        with self.assertRaisesRegex(ValueError,'disabled'): manager.load('user:userdemo')
        with self.assertRaises(ValueError): broad.resolve('user/userdemo/SKILL.md')

    def test_changed_during_read_and_hardlinked_skill_refused(self):
        original = os.read
        def mutate(fd, size):
            value = original(fd,size)
            self.skill.write_text(self.skill.read_text()+'changed')
            return value
        with patch('codine.skills.os.read',side_effect=mutate):
            with self.assertRaisesRegex(ValueError,'changed'): self.skills.load('project:demo')
        os.link(self.skill, self.skill.parent/'alias.md')
        with self.assertRaisesRegex(ValueError,'one link'): self.skills.load('project:demo')

    def test_fake_agent_load_then_reference_roundtrip_without_execution(self):
        from codine.agent import Agent
        from codine.config import Config, Redactor
        from codine.sessions import Session, SessionStore, validate_messages
        from codine.tools import build_registry
        registry = build_registry(self.ws,lambda:None)
        manager = self.ws.skills; manager.user_root=self.user
        requests=[]
        def call(name,args):
            return {'role':'assistant','content':None,'tool_calls':[{'id':f'call{len(requests)}','type':'function',
                    'function':{'name':name,'arguments':json.dumps(args)}}]}
        class FakeProvider:
            def complete(_,messages,schemas,model,emit):
                requests.append(copy.deepcopy(messages))
                if len(requests)==1:
                    self.assertIn('Explain code',json.dumps(messages))
                    self.assertNotIn('BODY_SENTINEL',json.dumps(messages))
                    return call('load_skill',{'name':'project:demo'}),'tool_calls',None
                result=json.loads(messages[-1]['content'])
                if len(requests)==2:
                    self.assertIn('BODY_SENTINEL',result['text'])
                    return call('read_skill_reference',{'name':'project:demo','path':'notes.md','expected_sha256':result['sha256']}),'tool_calls',None
                self.assertEqual(result['text'],'REFERENCE_SENTINEL')
                return {'role':'assistant','content':'Read instructions and reference; executed nothing.'},'stop',None
        store=SessionStore(self.project);self.addCleanup(store.close)
        session=Session.create(self.project,'fake','system')
        agent=Agent(Config(self.project),FakeProvider(),registry,store,session,Mock(),Redactor())
        agent.skill_manager=manager
        self.assertEqual(agent.run_turn('Use the demo skill.'),'completed')
        validate_messages(store.load(session.id).messages)
        self.ws.approve.assert_not_called()
        self.assertEqual(len(requests),3)

    def test_cli_switch_away_and_back_revokes_skill_and_clear_removes_context(self):
        from codine.agent import Agent
        from codine.cli import command
        from codine.config import Config, Redactor
        from codine.sessions import Session, SessionStore
        store=SessionStore(self.project);self.addCleanup(store.close)
        session=Session.create(self.project,'fake','system');store.save(session)
        agent=Agent(Config(self.project),Mock(),Mock(),store,session,Mock(),Redactor())
        agent.skill_manager=self.skills;self.ws.skills=self.skills
        row=self.skills.load('project:demo',activate=True)
        ui=Mock();ui.interactive=False
        self.assertIn('BODY_SENTINEL',self.skills.context())
        command('/clear',agent,self.ws,ui)
        self.assertNotIn('BODY_SENTINEL',self.skills.context())
        self.skills.load('project:demo',activate=True)
        command('/new',agent,self.ws,ui)
        command('/resume '+session.id,agent,self.ws,ui)
        with self.assertRaisesRegex(ValueError,'active session'):
            self.skills.reference('project:demo','notes.md',row['sha256'])

    def test_size_and_load_count_bounds(self):
        self.skill.write_bytes(b'x'*32769)
        with self.assertRaisesRegex(ValueError,'bounded'):self.skills.load('project:demo')
        self.skill.unlink();self.make(self.project/'.codine-skills','demo')
        for index in range(4):
            self.make(self.project/'.codine-skills',f'skill{index}')
            self.skills.load(f'project:skill{index}',activate=True)
        with self.assertRaisesRegex(ValueError,'four'):self.skills.load('project:demo')
        ref=self.project/'.codine-skills/skill0/notes.md';ref.write_bytes(b'x'*16385)
        with self.assertRaisesRegex(ValueError,'bounded'):
            self.skills.reference('project:skill0','notes.md',self.skills.loaded['project:skill0']['sha256'])

    def test_ignored_packages_and_references_never_enter_catalog_or_context(self):
        (self.project / '.gitignore').write_text('.codine-skills/demo/\n')
        result = self.skills.discover()
        self.assertEqual(result['skills'], [])
        self.assertNotIn('demo', json.dumps(result))
        self.assertNotIn('BODY_SENTINEL', self.skills.context())
        with self.assertRaisesRegex(ValueError, 'ignore'):
            self.skills.load('project:demo')
        (self.project / '.gitignore').write_text('')
        row = self.skills.load('project:demo')
        (self.skill.parent / '.gitignore').write_text('notes.md\n')
        with self.assertRaisesRegex(ValueError, 'ignore'):
            self.skills.reference('project:demo', 'notes.md', row['sha256'])
        self.assertEqual(self.ws.read_versions, {})

    def test_automatic_ignore_reads_never_prompt_or_bypass_denied_rules(self):
        from codine.command_policy import CommandPolicy, FileRule
        (self.project / '.gitignore').write_text('.codine-skills/demo/\n')
        self.ws.command_policy = CommandPolicy(self.root/'policy.json', file_rules=(
            FileRule(id='ask-ignore', action='ask', operation='read', project=str(self.project),
                     match='exact', path='.gitignore'),))
        self.assertEqual(self.skills.discover()['skills'], [])
        self.ws.approve.assert_not_called()
        with self.assertRaisesRegex(ValueError, 'ignore'):
            self.skills.load('project:demo')
        self.ws.approve.assert_called_once()
        self.assertEqual(self.ws.read_versions, {})

    def test_private_package_names_and_user_root_aliases_are_excluded(self):
        for scope, root in (('user', self.user), ('project', self.project / '.codine-skills')):
            self.make(root, 'id_rsa')
            with self.assertRaises(ValueError): self.skills.load(scope + ':id_rsa')
        self.assertNotIn('id_rsa', json.dumps(self.skills.discover()))
        broad = Workspace(self.root, Mock(return_value=True), Mock())
        Skills(broad, self.root / 'future' / 'skills')
        for name in ('FUTURE/SKILLS/demo/SKILL.md', 'future/Skills/demo/SKILL.md'):
            for operation in ('read', 'write'):
                with self.assertRaises(ValueError): broad.resolve(name, operation=operation)

    def test_import_rewind_and_fresh_manager_cannot_reuse_historical_receipt(self):
        from codine.agent import Agent
        from codine.config import Config, Redactor
        from codine.sessions import Session, SessionStore
        from codine.transcripts import command as transfer
        from codine.changesets import apply, rewind
        store = SessionStore(self.project); self.addCleanup(store.close)
        session = Session.create(self.project, 'fake', 'system'); store.save(session)
        agent = Agent(Config(self.project), Mock(), Mock(), store, session, Mock(), Redactor())
        agent.skill_manager = self.skills
        row = self.skills.load('project:demo', activate=True)
        # A historical valid tool pair carries a hash, not a new access receipt.
        session.messages.extend([
            {'role':'user', 'content':'Load the demo skill.'},
            {'role':'assistant','content':None,'tool_calls':[{'id':'old-load','type':'function',
                'function':{'name':'load_skill','arguments':'{"name":"project:demo"}'}}]},
            {'role':'tool','tool_call_id':'old-load','content':json.dumps(row)},
            {'role':'assistant','content':'Loaded historical skill.'}])
        ui = Mock(); ui.interactive = True
        transfer('export', 'history.json', agent, self.ws, ui, 'system')
        transfer('export', 'confirm ' + agent._transcript_review['digest'], agent, self.ws, ui, 'system')
        transfer('import', 'history.json', agent, self.ws, ui, 'system')
        transfer('import', 'confirm ' + agent._transcript_review['digest'], agent, self.ws, ui, 'system')
        self.assertIn('BODY_SENTINEL', json.dumps(agent.session.messages))
        with self.assertRaisesRegex(ValueError, 'active session'):
            self.skills.reference('project:demo', 'notes.md', row['sha256'])
        self.assertNotIn('BODY_SENTINEL', self.skills.context())
        target = self.project / 'app.py'; target.write_text('old\n')
        sha = self.ws.read_file('app.py')['sha256']
        result = apply(self.ws, [{'path':'app.py','content':'new\n','expected_sha256':sha}])
        self.skills.load('project:demo', activate=True)
        rewind(self.ws, result['changeset'])
        self.assertEqual(target.read_text(), 'old\n')
        with self.assertRaisesRegex(ValueError, 'active session'):
            self.skills.reference('project:demo', 'notes.md', row['sha256'])
        self.skills.load('project:demo', activate=True)
        restarted = Skills(Workspace(self.project, Mock(return_value=True)), self.user)
        with self.assertRaisesRegex(ValueError, 'active session'):
            restarted.reference('project:demo', 'notes.md', row['sha256'])
        self.assertNotIn('BODY_SENTINEL', restarted.context())

    def test_script_reference_is_text_and_does_not_grant_edit_or_execution(self):
        script = self.skill.parent / 'setup.py'
        script.write_text('from pathlib import Path\nPath("EXECUTED_MARKER").touch()\n')
        row = self.skills.load('project:demo')
        data = self.skills.reference('project:demo', 'setup.py', row['sha256'])
        self.assertIn('EXECUTED_MARKER', data['text'])
        self.assertFalse(data['execution_authorized'])
        self.assertFalse((self.project/'EXECUTED_MARKER').exists())
        self.assertEqual(self.ws.read_versions, {})
        self.assertEqual(self.ws.lifecycle_versions, {})
        self.ws.approve.assert_not_called()
        with self.assertRaises(ValueError):
            self.ws.edit_file('.codine-skills/demo/setup.py', 'touch()', 'unlink()')

    def test_combined_memory_skill_context_is_redacted_and_counts_toward_budget(self):
        from codine.agent import Agent
        from codine.config import Config, Redactor
        from codine.context_inspector import inspect_context
        from codine.instructions import InstructionManager
        from codine.project_memory import ProjectMemory, command as memory_command
        from codine.sessions import Session, SessionStore
        from codine.tools import build_registry
        store = SessionStore(self.project); self.addCleanup(store.close)
        session = Session.create(self.project, 'fake', 'system')
        provider = Mock()
        provider.complete.return_value = ({'role':'assistant','content':'done'}, 'stop', None)
        redactor = Redactor(['PRIVATE_KNOWN_KEY'])
        agent = Agent(Config(self.project), provider, build_registry(self.ws,lambda:None), store, session, Mock(), redactor)
        agent.instruction_manager = InstructionManager(self.ws, user_path=self.root/'absent-guidance')
        agent.project_memory = ProjectMemory(self.project, redactor)
        agent.skill_manager = self.skills
        self.skill.write_text(self.skill.read_text() + '\nPRIVATE_KNOWN_KEY ' + 'skilldata ' * 600)
        self.skills.load('project:demo', activate=True)
        ui = Mock(); ui.interactive = True
        memory_command('add "Remember approved fact"', agent, self.ws, ui)
        agent.config.context_chars = 1000
        self.assertEqual(inspect_context(agent)['status'], 'over_limit')
        provider.complete.assert_not_called()
        agent.config.context_chars = 160000
        self.assertEqual(agent.run_turn('Explain the loaded data.'), 'completed')
        sent = json.dumps(provider.complete.call_args.args[0])
        self.assertIn('Remember approved fact', sent)
        self.assertIn('BODY_SENTINEL', sent)
        self.assertIn('Lowest precedence', sent)
        self.assertIn('UNTRUSTED INSTRUCTION DATA', sent)
        self.assertNotIn('PRIVATE_KNOWN_KEY', sent)
