import copy
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from codine.config import Config, Redactor
from codine.context_inspector import inspect_context, format_context
from codine.registry import ToolRegistry
from codine.sessions import Session


class InspectorTests(unittest.TestCase):
    def test_projection_does_not_mutate_and_excludes_contents(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'AGENTS.md').write_text('PRIVATE INSTRUCTIONS')
            session = Session.create(root, 'offline', 'system')
            session.messages.extend([{'role': 'user', 'content': 'SECRET'}, {'role': 'assistant', 'content': 'answer'}])
            before = copy.deepcopy(session.messages)
            agent = SimpleNamespace(config=Config(root), session=session, registry=ToolRegistry(), redactor=Redactor())
            report = inspect_context(agent)
            self.assertEqual(report['status'], 'fits')
            self.assertEqual(session.messages, before)
            self.assertNotIn('SECRET', str(report))
            self.assertNotIn('PRIVATE INSTRUCTIONS', str(report))
            self.assertIn('next user message', format_context(report))
            agent.config.context_chars = 1
            self.assertEqual(inspect_context(agent)['status'], 'over_limit')
