import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from codine.artifacts import ArtifactStore
from codine.config import Redactor
from codine.historyview import details_command, groups, list_command, state
from codine.sessions import Session, SessionStore


def batch(name, content, ident='reused-id'):
    return [{'role': 'assistant', 'content': None, 'tool_calls': [
        {'id': ident, 'type': 'function', 'function': {'name': name, 'arguments': '{"path":"old.py"}'}}]},
        {'role': 'tool', 'tool_call_id': ident, 'content': content}]


class HistoryViewTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.session = Session.create(self.root, 'test', 'system')
        self.session.messages += batch('read_file', 'first result') + batch('run_command', 'second result')
        self.agent = SimpleNamespace(session=self.session, redactor=Redactor(['super-secret-token']), provider=Mock(), registry=Mock())
        self.ui = SimpleNamespace(write=Mock())

    def output(self):
        return self.ui.write.call_args.args[0]

    def test_group_matching_and_expansion_collapse(self):
        rows = groups(self.session)
        self.assertEqual([row['id'] for row in rows], ['G2', 'G4'])
        list_command('', self.agent, self.ui)
        self.assertIn('+ G2', self.output())
        self.assertNotIn('first result', self.output())
        list_command('expand G2', self.agent, self.ui)
        self.assertIn('first result', self.output())
        self.assertNotIn('second result', self.output())
        list_command('collapse G2', self.agent, self.ui)
        self.assertNotIn('first result', self.output())
        details_command('G2.1', self.agent, self.ui)
        self.assertIn('first result', self.output())
        self.assertNotIn('second result', self.output())
        self.agent.provider.complete.assert_not_called()
        self.agent.registry.execute.assert_not_called()

    def test_bounded_pages_and_terminal_controls_redacted_before_clipping(self):
        secret = 'super-secret-token'
        self.session.messages[-1]['content'] = 'x' * 3995 + secret + 'y' * 5000 + '\x1b[2J'
        details_command('', self.agent, self.ui)
        self.assertNotIn(secret, self.output())
        self.assertNotIn('super', self.output())
        self.assertLess(len(self.output()), 5000)
        details_command('next', self.agent, self.ui)
        self.assertNotIn('secret-token', self.output())
        self.assertNotIn('\x1b', self.output())
        self.assertEqual(state(self.agent).offset, 4000)
        details_command('prev', self.agent, self.ui)
        self.assertEqual(state(self.agent).offset, 0)

    def test_saved_history_survives_reload_and_compaction(self):
        self.session.context_start = len(self.session.messages)
        store = SessionStore(self.root); self.addCleanup(store.close)
        store.save(self.session)
        self.agent.session = store.load(self.session.id)
        list_command('expand G2', self.agent, self.ui)
        self.assertIn('omitted from active context', self.output())
        details_command('G2.1', self.agent, self.ui)
        self.assertIn('first result', self.output())
        self.agent.session = Session.create(self.root, 'new', 'system')
        details_command('next', self.agent, self.ui)
        self.assertIn('No recorded', self.output())
        self.assertIsNone(state(self.agent).selected)

    def test_pending_unknown_and_interrupted_outcomes_are_never_replayed(self):
        self.session.pending = {'reply': batch('write_file', '')[0], 'results': {}}
        list_command('expand PENDING', self.agent, self.ui)
        self.assertIn('outcome unresolved', self.output())
        details_command('PENDING.1', self.agent, self.ui)
        self.assertIn('does not execute or retry', self.output())
        self.assertEqual(self.session.pending['results'], {})
        self.agent.registry.execute.assert_not_called()

    def test_artifact_pages_redact_across_boundary_without_workspace_reads(self):
        store = ArtifactStore(self.root)
        content = 'x' * 3995 + 'super-secret-token' + 'z' * 9000
        ident = store.put('read_file', content)
        self.session.messages[2]['content'] = json.dumps({'truncated': True, 'artifact_id': ident, 'preview': 'old preview'})
        with patch('codine.workspace.Workspace._read', side_effect=AssertionError('Historical view must not read current files')):
            details_command('G2.1', self.agent, self.ui)
            self.assertNotIn('super', self.output())
            self.assertIn('Stored artifact snapshot', self.output())
            details_command('next', self.agent, self.ui)
            self.assertNotIn('secret-token', self.output())
        self.assertEqual(state(self.agent).offset, 4000)

    def test_missing_or_wrong_provenance_artifact_returns_saved_excerpt(self):
        ident = ArtifactStore(self.root).put('other_tool', 'private unrelated result')
        self.session.messages[2]['content'] = json.dumps({'truncated': True, 'artifact_id': ident, 'preview': 'saved excerpt'})
        details_command('G2.1', self.agent, self.ui)
        self.assertIn('saved excerpt', self.output())
        self.assertNotIn('private unrelated result', self.output())
        self.assertIsNone(state(self.agent).next_offset)
        self.session.messages[2]['content'] = json.dumps({'truncated': True, 'artifact_id': '0' * 64, 'preview': 'missing excerpt'})
        details_command('G2.1', self.agent, self.ui)
        self.assertIn('missing excerpt', self.output())

    def test_pagination_reaches_older_groups_and_bounds_expansion(self):
        for i in range(20):
            self.session.messages += batch('read_file', 'result' + str(i))
        list_command('2', self.agent, self.ui)
        self.assertIn('page 2/3', self.output())
        self.assertLess(self.output().count('calls ·'), 9)
        list_command('expand G2', self.agent, self.ui)
        self.assertIn('page 3/3', self.output())
        list_command('collapse all', self.agent, self.ui)
        self.assertFalse(state(self.agent).expanded)
        with self.assertRaises(ValueError): list_command('999', self.agent, self.ui)
        with self.assertRaises(ValueError): details_command('G2.999', self.agent, self.ui)
        with self.assertRaises(ValueError): details_command('G2.1 -1', self.agent, self.ui)

    def test_cli_uses_durable_history_instead_of_last_output_only(self):
        from codine.cli import command
        self.agent.store = Mock()
        self.ui.interactive = True
        command('/tools expand G2', self.agent, None, self.ui)
        self.assertIn('first result', self.output())
        command('/details G2.1', self.agent, None, self.ui)
        self.assertIn('first result', self.output())
        self.agent.provider.complete.assert_not_called()
        self.agent.store.save.assert_not_called()

    def test_actual_terminal_shows_expand_collapse_and_detail_navigation(self):
        import io
        from contextlib import redirect_stdout
        from codine.terminal import Terminal
        terminal = Terminal(self.agent.redactor, interactive=False)
        output = io.StringIO()
        with redirect_stdout(output):
            list_command('expand G2', self.agent, terminal)
            details_command('G2.1', self.agent, terminal)
            list_command('collapse G2', self.agent, terminal)
        rendered = output.getvalue()
        self.assertIn('G2.1', rendered)
        self.assertIn('first result', rendered)
        self.assertIn('+ G2', rendered)
        self.assertIn('Historical output only', rendered)
