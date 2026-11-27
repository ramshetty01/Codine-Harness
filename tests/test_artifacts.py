import hashlib
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from codine.agent import Agent
from codine.artifacts import ArtifactStore, register_artifact_tool
from codine.config import Config, Redactor
from codine.registry import ToolRegistry, ToolSpec
from codine.sessions import Session, SessionStore
from codine.workspace import Workspace


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.workspace = Workspace(self.root, lambda _: False)
        self.store = ArtifactStore(self.root, self.workspace)

    def test_full_pages_dedup_private_mode_and_stale_source(self):
        text = 'alpha 🥭\n' * 3000
        path = self.root / 'a.txt'
        path.write_text(text)
        result = json.dumps({'path': 'a.txt', 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'content': text})
        ident = self.store.put('read_file', result)
        self.assertEqual(ident, self.store.put('read_file', result))
        snapshot = self.root / '.codine/artifacts' / (ident + '.json')
        self.assertEqual(snapshot.stat().st_mode & 0o777, 0o600)
        offset, parts = 0, []
        while offset is not None:
            page = self.store.read(ident, offset, 6000)
            self.assertFalse(page['source_changed'])
            parts.append(page['content'])
            offset = page['next_offset']
        self.assertEqual(''.join(parts), result)
        path.write_text('changed')
        self.assertTrue(self.store.read(ident)['source_changed'])
        self.assertEqual(self.workspace.read_versions, {})

    def test_tampering_links_and_quota_fail_explicitly(self):
        ident = self.store.put('command', 'long output')
        path = self.root / '.codine/artifacts' / (ident + '.json')
        path.write_text('{}')
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.store.read(ident)
        path.unlink()
        secret = self.root / '.env'
        secret.write_text('PRIVATE')
        path.symlink_to(secret)
        with self.assertRaises(OSError):
            self.store.read(ident)
        with patch('codine.artifacts.MAX_FILE', 100):
            result = json.loads(self.store.offload('command', 'x' * 20000))
        self.assertIn('artifact_error', result)
        self.assertNotIn('artifact_id', result)
        self.assertTrue(result['truncated'])

    def test_agent_keeps_loop_detection_on_full_result_before_offload(self):
        session = Session.create(self.root, 'offline', 'system')
        sessions = SessionStore(self.root)
        self.addCleanup(sessions.close)
        registry = ToolRegistry()
        counter = {'n': 0}
        def output():
            counter['n'] += 1
            return {'stdout': 'SECRET' + 'x' * 20000, 'seconds': counter['n'], 'exit_code': 0}
        registry.register(ToolSpec('large', 'large', {'type': 'object'}, output))
        class Provider:
            def complete(self, messages, schemas, model, emit):
                return ({'role': 'assistant', 'content': None, 'tool_calls': [{'id': str(counter['n']), 'type': 'function',
                         'function': {'name': 'large', 'arguments': '{}'}}]}, 'tool_calls', None)
        agent = Agent(Config(self.root), Provider(), registry, sessions, session, lambda *args: None, Redactor(['SECRET']))
        agent.artifact_store = self.store
        register_artifact_tool(registry, self.store)
        self.assertEqual(agent.run_turn('inspect output'), 'loop_limit')
        results = [json.loads(row['content']) for row in session.messages if row['role'] == 'tool']
        self.assertEqual(len(results), 3)
        self.assertTrue(all('artifact_id' in row for row in results))
        self.assertTrue(all(len(json.dumps(row)) < 5000 for row in results))
        self.assertTrue(all('SECRET' not in path.read_text() for path in (self.root / '.codine/artifacts').glob('*.json')))

    def test_quota_preserves_existing_references(self):
        ident = self.store.put('command', 'original')
        with patch('codine.artifacts.MAX_FILES', 1):
            with self.assertRaisesRegex(ValueError, 'quota'):
                self.store.put('command', 'new')
        self.assertEqual(self.store.read(ident)['content'], 'original')
