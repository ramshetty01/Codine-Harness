import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
from codine.config import Redactor
from codine.sessions import Session, SessionStore
from codine.task_plan import update_plan, clear_plan, plan_context, register_plan_tool
from codine.tools import build_registry
from codine.workspace import Workspace


class TaskPlanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.store = SessionStore(self.root)
        self.addCleanup(self.store.close)
        self.agent = SimpleNamespace(session=Session.create(self.root, 'offline', 'system'), redactor=Redactor(['SECRET']))
        self.agent.save = lambda: self.store.save(self.agent.session)

    def test_progress_survives_resume_and_partial_updates_preserve_items(self):
        update_plan(self.agent, [{'id': 'one', 'title': 'Implement', 'status': 'in_progress'},
                                 {'id': 'two', 'title': 'Test', 'status': 'pending'}], 0)
        update_plan(self.agent, [{'id': 'one', 'title': 'Implement', 'status': 'completed', 'evidence': 'Diff reviewed'},
                                 {'id': 'two', 'title': 'Test', 'status': 'blocked', 'reason': 'Missing runtime'}], 1)
        saved = self.store.load(self.agent.session.id)
        self.assertEqual(saved.plan_revision, 2)
        self.assertEqual(saved.plan[1]['reason'], 'Missing runtime')
        self.assertIn('Missing runtime', plan_context(saved))
        with self.assertRaisesRegex(ValueError, 'Stale'):
            update_plan(self.agent, [], 0)
        before = copy.deepcopy(saved.plan)
        with self.assertRaisesRegex(ValueError, 'evidence'):
            update_plan(self.agent, [{'id': 'two', 'title': 'Test', 'status': 'completed'}], 2)
        self.assertEqual(self.agent.session.plan, before)
        update_plan(self.agent, [{'id': 'three', 'title': 'SECRET follow-up', 'status': 'pending'}], 2)
        self.assertEqual(len(self.agent.session.plan), 3)
        self.assertNotIn('SECRET', str(self.agent.session.plan))

    def test_save_failure_restores_in_memory_state_and_clear_keeps_revision(self):
        update_plan(self.agent, [{'id': 'a', 'title': 'Do work', 'status': 'pending'}], 0)
        old = copy.deepcopy(self.agent.session.plan)
        original = self.agent.save
        self.agent.save = Mock(side_effect=OSError('disk full'))
        with self.assertRaises(OSError):
            clear_plan(self.agent)
        self.assertEqual(self.agent.session.plan, old)
        self.assertEqual(self.agent.session.plan_revision, 1)
        self.agent.save = original
        clear_plan(self.agent)
        self.assertEqual(self.store.load(self.agent.session.id).plan, [])
        self.assertIn('"revision": 2', plan_context(self.agent.session))

    def test_plan_tool_works_in_read_only_mode_without_project_writes(self):
        workspace = Workspace(self.root, lambda _: False, lambda _: None)
        workspace.mode = 'plan'
        registry = build_registry(workspace, lambda: None)
        register_plan_tool(registry, self.agent)
        names = [row['function']['name'] for row in registry.schemas()]
        self.assertIn('update_plan', names)
        self.assertNotIn('edit_file', names)
        call = {'function': {'name': 'update_plan', 'arguments': json.dumps({'expected_revision': 0, 'items': [
            {'id': 'a', 'title': 'Inspect', 'status': 'pending'}]})}}
        self.assertEqual(json.loads(registry.execute(call))['revision'], 1)
        self.assertEqual([p.name for p in self.root.iterdir()], ['.codine'])
