import io
import json
import unittest
from unittest.mock import patch
from codine.router_catalog import metadata, tool_models
from model_picker import model_label

class CatalogTests(unittest.TestCase):
    def test_paid_tool_models_sorted_and_invalid_prices_rejected(self):
        data=[{'id':'paid','supported_parameters':['tools'],'pricing':{'prompt':'.000001','completion':'.000002'}},
              {'id':'free','supported_parameters':['tools'],'pricing':{'prompt':'0','completion':'0'}},
              {'id':'bad','supported_parameters':['tools'],'pricing':{'prompt':'NaN','completion':'0'}},
              {'id':'chat','supported_parameters':[],'pricing':{'prompt':'0','completion':'0'}}]
        self.assertEqual([row['id'] for row in tool_models(data)],['free','paid'])

    def test_metadata_errors_do_not_disclose_credentials(self):
        import urllib.error
        with patch('urllib.request.urlopen',side_effect=urllib.error.URLError('secret-key')):
            with self.assertRaisesRegex(ValueError,'metadata unavailable') as error: metadata('key','secret-key')
        self.assertNotIn('secret-key',str(error.exception))

    def test_label_includes_per_million_prices(self):
        label=model_label({'id':'model','pricing':{'prompt':'.000001','completion':'.000002'},'context_length':32000})
        self.assertIn('$1',label)
        self.assertIn('$2',label)
        self.assertIn('32,000',label)

    def test_picker_changes_session_model_without_resetting_budget(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from codine.cli import command
        import tempfile
        from pathlib import Path
        from codine.sessions import Session
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        root=Path(temporary.name).resolve()
        session=Session.create(root,'old','system',provider='openrouter')
        billing=session.billing
        agent=SimpleNamespace(config=SimpleNamespace(provider='openrouter'),
                              session=session,shared_budget_path=root/'unused-ledger.json',save=Mock())
        ui=SimpleNamespace(interactive=True,write=Mock())
        row={'id':'free/model','pricing':{'prompt':'0','completion':'0'},
             'architecture':{'input_modalities':['text'],'output_modalities':['text']},'supported_parameters':['tools']}
        from codine.catalog_cache import CatalogSnapshot
        with patch('codine.router_catalog.picker_catalog',return_value=(CatalogSnapshot([row], 0, False, 'live'), '')), patch('model_picker.select_model',return_value='free/model'):
            command('/model free',agent,None,ui)
        self.assertEqual(agent.session.model,'free/model')
        self.assertIs(agent.session.billing,billing)
        agent.save.assert_called_once()
