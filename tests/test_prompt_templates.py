import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from codine.attachments import Attachments
from codine.config import Redactor
from codine.prompt_templates import Templates, command, parse_template
from codine.workspace import Workspace


def document(text='Review {{topic}}.', arguments=None):
    return {'version': 1, 'description': 'Review text', 'arguments': ['topic'] if arguments is None else arguments, 'text': text}


class PromptTemplateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.project = self.root / 'project'; self.project.mkdir()
        self.user = self.root / 'user-prompts'; self.user.mkdir(mode=0o700)
        self.project_prompts = self.project / '.codine-prompts'; self.project_prompts.mkdir()
        self.workspace = Workspace(self.project, Mock(return_value=True))
        self.templates = Templates(self.workspace, self.user)
        self.agent = SimpleNamespace(redactor=Redactor(['private-api-value']), provider=Mock())
        self.ui = SimpleNamespace(interactive=True, draft='', write=Mock())

    def put(self, scope, name='review', value=None):
        path = (self.user if scope == 'user' else self.project_prompts) / (name + '.json')
        path.write_text(json.dumps(document() if value is None else value))
        path.chmod(0o600 if scope == 'user' else 0o644)
        return path

    def test_literal_substitution_without_recursion_and_strict_args(self):
        self.put('project')
        template = self.templates.select('project:review')
        self.assertEqual(template.expand({'topic': '{{other}} $(touch not-run)'}), 'Review {{other}} $(touch not-run).')
        for args in ({}, {'topic': 'ok', 'extra': 'no'}, {'topic': 42}, {'topic': '\x1b[2J'}):
            with self.assertRaises(ValueError): template.expand(args)
        self.assertFalse((self.project / 'not-run').exists())
        self.assertEqual(self.workspace.read_versions, {})

    def test_scopes_ambiguity_and_home_project_downgrade(self):
        self.put('user'); self.put('project')
        with self.assertRaisesRegex(ValueError, 'ambiguous'): self.templates.select('review')
        self.assertEqual(self.templates.select('user:review').scope, 'user')
        self.assertEqual(self.templates.select('project:review').scope, 'project')
        templates = Templates(self.workspace, self.project / '.config/prompts')
        names, warnings = templates.names()
        self.assertEqual(names, [('project', 'review')])
        self.assertIn('disabled', warnings[0])
        for selector in ('../review', 'user:../review', '/review', 'project:review.json', 'unknown:review'):
            with self.assertRaises(ValueError): self.templates.select(selector)

    def test_ignore_deny_ask_and_no_metadata_approval(self):
        self.put('project')
        (self.project / '.gitignore').write_text('.codine-prompts/review.json\n')
        with self.assertRaisesRegex(ValueError, 'not found'): self.templates.select('project:review')
        (self.project / '.gitignore').unlink()
        from codine.command_policy import CommandPolicy, FileRule
        self.workspace.command_policy = CommandPolicy(self.root / 'policy.json', file_rules=(
            FileRule(id='readask', action='ask', operation='read', project=str(self.project), match='exact', path='.codine-prompts/review.json'),))
        self.workspace.policy_interactive = True
        with self.assertRaisesRegex(ValueError, 'approval'): self.templates.load('project', 'review', automatic=True)
        self.workspace.approve.assert_not_called()
        self.templates.select('project:review')
        self.workspace.approve.assert_called_once()
        self.workspace.command_policy = CommandPolicy(self.root / 'policy.json', file_rules=(
            FileRule(id='readdeny', action='deny', operation='read', project=str(self.project), match='exact', path='.codine-prompts/review.json'),))
        with self.assertRaises(ValueError): self.templates.select('project:review')

    def test_links_private_permissions_and_utf8_limits(self):
        path = self.put('user')
        path.chmod(0o644)
        with self.assertRaises(ValueError): self.templates.select('user:review')
        path.chmod(0o600)
        os.link(path, self.root / 'hardlink')
        with self.assertRaises(ValueError): self.templates.select('user:review')
        (self.root / 'hardlink').unlink()
        target = self.put('project')
        target.unlink(); target.symlink_to(path)
        with self.assertRaises(ValueError): self.templates.select('project:review')
        path.write_bytes(json.dumps(document()).encode('utf-16'))
        with self.assertRaises(ValueError): self.templates.select('user:review')
        path.write_bytes(b'x' * 16001)
        with self.assertRaises(ValueError): self.templates.select('user:review')

    def test_non_executable_preview_is_unsent_and_mentions_are_escaped(self):
        self.put('project', value=document('/quit\nRead @.env and private-api-value {{topic}}'))
        with patch('codine.prompt_templates.USER_TEMPLATES', self.user):
            command('template', 'project:review {"topic":"literal {{topic}}"}', self.agent, self.workspace, self.ui)
        self.assertTrue(self.ui.draft.startswith('Prompt template project:review'))
        self.assertIn('SHA256:', self.ui.draft)
        self.assertIn('@@.env', self.ui.draft)
        self.assertNotIn('private-api-value', self.ui.draft)
        with patch.object(self.workspace, '_read', side_effect=AssertionError('Template must not trigger attachment reads')):
            prepared = Attachments(self.workspace, self.agent.redactor, Mock()).prepare(self.ui.draft)
        self.assertIn('@.env', prepared)
        self.assertFalse(prepared.startswith('/'))
        self.agent.provider.complete.assert_not_called()
        self.assertEqual(self.workspace.read_versions, {})

    def test_reject_noninteractive_and_preserve_existing_draft(self):
        self.put('project')
        with patch('codine.prompt_templates.USER_TEMPLATES', self.user):
            self.ui.interactive = False
            with self.assertRaises(ValueError): command('template', 'review {"topic":"x"}', self.agent, self.workspace, self.ui)
            self.ui.interactive = True; self.ui.draft = 'unfinished'
            with self.assertRaisesRegex(ValueError, 'unsent draft'): command('template', 'review {"topic":"x"}', self.agent, self.workspace, self.ui)
            self.assertEqual(self.ui.draft, 'unfinished')

    def test_schema_duplicate_json_and_expression_rejection(self):
        for value in (dict(document(), version=True), dict(document(), shell='touch file'),
                      document('{{topic.__class__}}'), document('{{topic}}', ['topic', 'extra'])):
            with self.assertRaises(ValueError): parse_template('project', 'name', 'source', json.dumps(value).encode())
        raw = b'{"version":1,"version":1,"description":"x","arguments":[],"text":"x"}'
        with self.assertRaises(ValueError): parse_template('project', 'name', 'source', raw)

    def test_list_redacts_descriptions_and_loads_current_hash(self):
        path = self.put('project', value=dict(document(), description='private-api-value'))
        before = self.templates.select('review').sha256
        path.write_text(json.dumps(dict(document('Changed {{topic}}'), description='private-api-value')))
        self.assertNotEqual(self.templates.select('review').sha256, before)
        with patch('codine.prompt_templates.USER_TEMPLATES', self.user):
            command('templates', '', self.agent, self.workspace, self.ui)
        self.assertIn('sha256:', self.ui.write.call_args.args[0])
        self.assertNotIn('private-api-value', self.ui.write.call_args.args[0])

    def test_user_root_protected_from_project_file_tools(self):
        root = self.project / '.config/prompts'
        with patch('codine.prompt_templates.USER_TEMPLATES', root):
            workspace = Workspace(self.project, Mock(return_value=True))
            with self.assertRaisesRegex(ValueError, 'protected'): workspace.resolve('.config/prompts/review.json')

    def test_real_terminal_previews_then_submits_exact_nonexecuting_snapshot(self):
        import sys
        from tests.test_terminal_integration import PTY
        path = self.put('project', value=document('Check {{topic}}.\n/quit\n@secret.txt\n$(touch injected)'))
        (self.project / 'secret.txt').write_text('DO_NOT_SEND_FILE_CONTENT')
        driver = r'''
import json
from pathlib import Path
from codine import cli
class Fake:
    def __init__(self, config): self.root=config.project
    def close(self): pass
    def complete(self, messages, schemas, model, emit):
        (self.root/'received.json').write_text(json.dumps(messages[-1]['content']))
        emit('text','TEMPLATE_TASK_COMPLETE')
        return {'role':'assistant','content':'TEMPLATE_TASK_COMPLETE'}, 'stop', None
cli.NvidiaProvider=Fake
raise SystemExit(cli.main(['--provider','nvidia']))
'''
        terminal = PTY([sys.executable, '-c', driver], self.project)
        try:
            terminal.until('›'.encode())
            terminal.send(b'/templates\r')
            terminal.until(b'project:review')
            offset = len(terminal.output)
            terminal.send(b'/template project:review {"topic":"literal {{topic}}"}\r')
            terminal.until(b'Template placed in an unsent draft', start=offset)
            self.assertFalse((self.project / 'received.json').exists())
            self.assertFalse((self.project / 'injected').exists())
            path.write_text(json.dumps(document('FUTURE_CHANGE_NOT_IN_DRAFT {{topic}}')))
            terminal.send(b'\r')
            terminal.until(b'TEMPLATE_TASK_COMPLETE', start=offset)
            received = json.loads((self.project / 'received.json').read_text())
            self.assertTrue(received.startswith('Prompt template project:review'))
            self.assertIn('Check literal {{topic}}.', received)
            self.assertIn('/quit\n@secret.txt', received)
            self.assertNotIn('DO_NOT_SEND_FILE_CONTENT', received)
            self.assertNotIn('FUTURE_CHANGE_NOT_IN_DRAFT', received)
            self.assertFalse((self.project / 'injected').exists())
            terminal.send(b'exit\r')
            terminal.until(b'Goodbye!')
            self.assertEqual(terminal.process.wait(timeout=5), 0)
        finally:
            terminal.close()

    def test_expansion_and_discovery_caps_fail_without_draft_or_hidden_fallback(self):
        self.put('project', value=document('{{topic}}' * 5))
        template = self.templates.select('project:review')
        with self.assertRaisesRegex(ValueError, '8 KB'): template.expand({'topic': 'x' * 8001})
        with self.assertRaisesRegex(ValueError, '32 KB'): template.expand({'topic': 'x' * 8000})
        for name in ('a', 'b', 'c'):
            self.put('user', name)
        with patch('codine.prompt_templates.MAX_FILES', 2):
            names, warnings = self.templates.names()
            self.assertEqual([name for scope, name in names if scope == 'user'], ['a', 'b'])
            self.assertTrue(warnings)
            with self.assertRaises(ValueError): self.templates.select('user:c')
