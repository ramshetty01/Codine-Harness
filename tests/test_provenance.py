import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from codine.provenance import evaluation_manifest


class ProvenanceTests(unittest.TestCase):
    def test_determinism_and_sensitive_exclusions(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'agent.py').write_text('x = 1\n')
            (root / '.env').write_text('KEY=private-key')
            cfg = SimpleNamespace(model='test', api_key='private-key', project=root)
            tasks = [{'id': 'one', 'reference': 'hidden-answer'}]
            first = evaluation_manifest(tasks, mode='live', config=cfg, source_root=root)
            self.assertEqual(first, evaluation_manifest(tasks, mode='live', config=cfg, source_root=root))
            self.assertNotIn('private-key', json.dumps(first))
            self.assertNotIn('hidden-answer', json.dumps(first))
            for change in ('source', 'task', 'config', 'image'):
                with self.subTest(change=change):
                    altered_tasks = tasks
                    image = None
                    if change == 'source':
                        (root / 'agent.py').write_text('x = 2\n')
                    elif change == 'task':
                        altered_tasks = [{'id': 'one', 'reference': 'different'}]
                    elif change == 'config':
                        cfg.model = 'other'
                    else:
                        image = 'sha256:other'
                    altered = evaluation_manifest(altered_tasks, mode='live', config=cfg, image=image, source_root=root)
                    self.assertNotEqual(first['sha256'], altered['sha256'])
                    (root / 'agent.py').write_text('x = 1\n')
                    cfg.model = 'test'
