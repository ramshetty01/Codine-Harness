import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
from codine.notebooks import Notebooks
from codine.workspace import Workspace
from codine.tools import build_registry


class NotebookTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.ws = Workspace(self.root, Mock(return_value=True), Mock(), track_changes=True)
        self.nb = Notebooks(self.ws)
        self.path = self.root / 'analysis.ipynb'
        self.document = {'nbformat': 4, 'nbformat_minor': 5, 'metadata': {'kernelspec': {'name': 'python3'}},
            'cells': [{'id': 'intro', 'cell_type': 'markdown', 'metadata': {'tags': ['keep']}, 'source': ['# Analysis\n', 'Read carefully']},
                      {'id': 'compute', 'cell_type': 'code', 'metadata': {'custom': True}, 'source': 'print(1)\n',
                       'execution_count': 5, 'outputs': [{'output_type': 'display_data', 'data': {'text/html': '<script>evil()</script>'}}]}]}
        self.path.write_text(json.dumps(self.document))

    def test_cells_are_structured_not_executed_and_no_generic_edit_grants(self):
        result = self.nb.read('analysis.ipynb', cell=1)
        self.assertEqual(result['source'], 'print(1)\n')
        self.assertEqual(result['output_count'], 1)
        self.assertNotIn('<script>', json.dumps(result))
        self.assertFalse(self.ws.read_versions)
        self.assertFalse(self.ws.read_ranges)
        self.assertFalse(self.ws.lifecycle_versions)

    def test_edit_preserves_unrelated_data_clears_stale_output_and_can_rewind(self):
        result = self.nb.read('analysis.ipynb', cell=1)
        before = self.path.read_bytes()
        change = self.nb.edit('analysis.ipynb', 1, 'print(2)\n', result['sha256'])
        self.assertFalse(change['executed'])
        edited = json.loads(self.path.read_text())
        self.assertEqual(edited['metadata'], self.document['metadata'])
        self.assertEqual(edited['cells'][0], self.document['cells'][0])
        self.assertEqual(edited['cells'][1]['metadata'], self.document['cells'][1]['metadata'])
        self.assertEqual(edited['cells'][1]['outputs'], [])
        self.assertIsNone(edited['cells'][1]['execution_count'])
        from codine.changesets import rewind
        rewind(self.ws, self.ws.change_group_id)
        self.assertEqual(self.path.read_bytes(), before)

    def test_incomplete_read_and_cross_session_grants_rejected(self):
        result = self.nb.read('analysis.ipynb', cell=0, max_chars=3)
        with self.assertRaisesRegex(ValueError, 'entire'):
            self.nb.edit('analysis.ipynb', 0, 'replacement', result['sha256'])
        self.nb.read('analysis.ipynb', cell=0, start_char=3, expected_sha256=result['sha256'])
        self.ws.change_session_id = 'different-session'
        with self.assertRaisesRegex(ValueError, 'entire'):
            self.nb.edit('analysis.ipynb', 0, 'replacement', result['sha256'])

    def test_paging_staleness_and_approval_race_preserve_external_edit(self):
        result = self.nb.read('analysis.ipynb', cell=1)
        self.path.write_text(json.dumps({**self.document, 'metadata': {'external': True}}))
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.nb.read('analysis.ipynb', expected_sha256=result['sha256'])
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.nb.edit('analysis.ipynb', 1, 'print(2)', result['sha256'])
        result = self.nb.read('analysis.ipynb', cell=1)
        def approval(_):
            self.path.write_text('external writer')
            return True
        self.ws.approve = approval
        with self.assertRaises(ValueError):
            self.nb.edit('analysis.ipynb', 1, 'print(2)', result['sha256'])
        self.assertEqual(self.path.read_text(), 'external writer')

    def test_decline_and_planning_preserve_file(self):
        result = self.nb.read('analysis.ipynb', cell=1)
        before = self.path.read_bytes()
        self.ws.approve = Mock(return_value=False)
        self.assertEqual(self.nb.edit('analysis.ipynb', 1, 'other', result['sha256'])['status'], 'declined')
        self.assertEqual(self.path.read_bytes(), before)
        self.ws.mode = 'plan'
        with self.assertRaises(ValueError): self.nb.edit('analysis.ipynb', 1, 'other', result['sha256'])
        registry = build_registry(self.ws, lambda: None)
        self.assertIn('read_notebook', registry.tools)
        self.assertFalse(registry.allowed(registry.tools['edit_notebook_cell']))

    def test_bad_format_limits_and_private_paths(self):
        for text in ('{"nbformat":4,"nbformat":3}', '{"nbformat":3,"cells":[]}',
                     json.dumps({**self.document, 'cells': self.document['cells'] * 501}),
                     '{"nbformat":4,"cells":[],"metadata":{"value":NaN}}'):
            self.path.write_text(text)
            with self.assertRaises(ValueError): self.nb.read('analysis.ipynb')
        self.path.write_text(json.dumps(self.document))
        (self.root/'linked.ipynb').symlink_to(self.path)
        with self.assertRaises(ValueError): self.nb.read('linked.ipynb')
        with self.assertRaises(ValueError): self.nb.read('../outside.ipynb')
        with self.assertRaises(ValueError): self.nb.read('.env')

    def test_empty_notebook_and_source_representations(self):
        self.path.write_text(json.dumps({**self.document, 'cells': []}))
        self.assertEqual(self.nb.read('analysis.ipynb')['total_cells'], 0)
        self.path.write_text(json.dumps(self.document))
        result = self.nb.read('analysis.ipynb', cell=0)
        self.nb.edit('analysis.ipynb', 0, '# Changed\nbody', result['sha256'])
        self.assertEqual(json.loads(self.path.read_text())['cells'][0]['source'], ['# Changed\n', 'body'])
