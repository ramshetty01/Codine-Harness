import json
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from codine.catalog_cache import CatalogCache, TTL_SECONDS
from codine.router_catalog import picker_catalog
from model_picker import free_tool_models

ROW = {'id': 'vendor/model', 'supported_parameters': ['tools'],
       'context_length': 32000, 'top_provider': {'max_completion_tokens': 1024},
       'architecture': {'input_modalities': ['text'], 'output_modalities': ['text']},
       'pricing': {'prompt': '0', 'completion': '0'}}


class CatalogCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / 'catalogs'
        self.cache = CatalogCache(self.root)

    def test_fresh_cache_avoids_network_and_preserves_capabilities(self):
        self.cache.save([dict(ROW, secret='never persisted')])
        with patch('codine.router_catalog.metadata', side_effect=AssertionError('No network')):
            snapshot, warning = picker_catalog(cache=self.cache)
        self.assertFalse(snapshot.stale)
        self.assertEqual(warning, '')
        self.assertEqual(self.cache.model_record('vendor/model')['top_provider']['max_completion_tokens'], 1024)
        self.assertNotIn('secret', self.cache.path.read_text())
        self.assertEqual(self.cache.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.root.stat().st_mode & 0o777, 0o700)

    def test_failed_refresh_never_implicitly_uses_stale(self):
        self.cache.save([ROW], fetched_at=time.time() - TTL_SECONDS - 5)
        with patch('codine.router_catalog.metadata', side_effect=ValueError('offline')):
            with self.assertRaisesRegex(ValueError, 'Model unchanged'):
                picker_catalog(cache=self.cache)
        with patch('codine.router_catalog.metadata', side_effect=AssertionError('No network')):
            snapshot, _ = picker_catalog('cached', cache=self.cache)
        self.assertTrue(snapshot.stale)
        self.assertIn('STALE', snapshot.describe())
        with self.assertRaises(ValueError): self.cache.model_record('vendor/model')

    def test_scope_tamper_future_and_unsafe_files_rejected(self):
        self.cache.save([ROW])
        other = CatalogCache(self.root, endpoint='https://example.test/v1/models')
        self.assertIsNone(other.load())
        other.path.write_bytes(self.cache.path.read_bytes()); other.path.chmod(0o600)
        with self.assertRaisesRegex(ValueError, 'scope'): other.load()
        self.cache.path.chmod(0o644)
        with self.assertRaises(ValueError): self.cache.load()
        self.cache.path.unlink(); self.cache.path.symlink_to(other.path)
        with self.assertRaises(OSError): self.cache.load()
        with self.assertRaises(ValueError): self.cache.save([ROW], fetched_at=time.time() + 1000)

    def test_atomic_replacement_failure_keeps_prior_catalog(self):
        self.cache.save([ROW]); prior = self.cache.path.read_bytes()
        with patch('codine.catalog_cache.os.replace', side_effect=OSError('disk problem')):
            with self.assertRaises(OSError): self.cache.save([dict(ROW, id='another')])
        self.assertEqual(self.cache.path.read_bytes(), prior)
        self.assertEqual(list(self.root.glob('.catalog-*')), [])

    def test_live_catalog_usable_when_cache_write_fails(self):
        with patch('codine.router_catalog.metadata', return_value=[ROW]), patch.object(self.cache, 'save', side_effect=OSError('disk full')):
            snapshot, warning = picker_catalog('refresh', cache=self.cache)
        self.assertEqual(snapshot.source, 'live')
        self.assertIn('could not be cached', warning)

    def test_bounded_schema_and_missing_price_not_free(self):
        with self.assertRaises(ValueError): self.cache.save([ROW] * 10001)
        with self.assertRaises(ValueError): self.cache.save([dict(ROW, supported_parameters='tools')])
        self.cache.save([dict(ROW, pricing={})])
        self.assertEqual(free_tool_models(self.cache.load().records), [])
        self.cache.path.write_text('[' * 2000 + '0' + ']' * 2000)
        with self.assertRaises(ValueError): self.cache.load()

    def test_cli_manual_selection_without_network_keeps_provider_budget(self):
        from codine.cli import command
        billing = {'spent': '1.00', 'pending': '.25'}
        agent = SimpleNamespace(config=SimpleNamespace(provider='openrouter'), session=SimpleNamespace(model='old', billing=billing), save=Mock())
        ui = SimpleNamespace(interactive=True, write=Mock())
        with patch('codine.router_catalog.metadata', side_effect=AssertionError('No network')):
            command('/model explicit/model', agent, None, ui)
        self.assertEqual(agent.session.model, 'explicit/model')
        self.assertEqual(agent.config.provider, 'openrouter')
        self.assertIs(agent.session.billing, billing)

    def test_cli_failure_does_not_change_model_or_save(self):
        from codine.cli import command
        agent = SimpleNamespace(config=SimpleNamespace(provider='openrouter'), session=SimpleNamespace(model='old'), save=Mock())
        ui = SimpleNamespace(interactive=True, write=Mock())
        with patch('codine.router_catalog.picker_catalog', side_effect=ValueError('offline')):
            with self.assertRaises(ValueError): command('/model refresh', agent, None, ui)
        self.assertEqual(agent.session.model, 'old')
        agent.save.assert_not_called()

    def test_live_price_reads_do_not_consult_cache(self):
        from codine.router_catalog import metadata
        import io
        raw = io.BytesIO(b'{"data":{"remaining":1}}')
        with patch.object(CatalogCache, 'load', side_effect=AssertionError('Billing must not use cache')), patch('urllib.request.urlopen', return_value=raw):
            self.assertEqual(metadata('key'), {'remaining': 1})
