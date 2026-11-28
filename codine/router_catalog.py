"""Bounded OpenRouter metadata reads. These endpoints do not run inference."""
import json
import time
import urllib.error
import urllib.request
from .budget import money
from .failure_policy import retry_metadata_read


def metadata(endpoint, api_key=''):
    if endpoint not in ('models', 'key', 'credits'):
        raise ValueError('Unsupported metadata endpoint.')
    headers = {'Accept': 'application/json'}
    if api_key:
        headers['Authorization'] = 'Bearer ' + api_key
    request = urllib.request.Request('https://openrouter.ai/api/v1/' + endpoint, headers=headers)
    def read(timeout):
        deadline = time.monotonic() + timeout
        raw = bytearray()
        with urllib.request.urlopen(request, timeout=timeout) as response:
            while True:
                if time.monotonic() >= deadline:
                    raise TimeoutError('Metadata response deadline exceeded.')
                chunk = response.read1(min(65536, 8_000_001 - len(raw)))
                if not chunk:
                    break
                raw.extend(chunk)
                if len(raw) > 8_000_000:
                    raise ValueError('Catalog response exceeds local limit.')
        return json.loads(raw)['data']
    try:
        return retry_metadata_read(read)
    except (OSError, KeyError, TypeError, ValueError):
        raise ValueError('OpenRouter metadata unavailable; no inference requested.') from None


def tool_models(data):
    if not isinstance(data, list):
        raise ValueError('Malformed model catalog.')
    result = []
    for row in data:
        if not isinstance(row, dict) or not isinstance(row.get('id'), str):
            continue
        if 'tools' not in (row.get('supported_parameters') or []) or ':batch' in row.get('id', ''):
            continue
        architecture = row.get('architecture') or {}
        if architecture and ('text' not in architecture.get('input_modalities', []) or 'text' not in architecture.get('output_modalities', [])):
            continue
        try:
            pricing = row['pricing']
            prompt, completion = money(pricing['prompt']), money(pricing['completion'])
        except (KeyError, TypeError, ValueError):
            continue
        result.append({'id': row['id'], 'prompt': str(prompt), 'completion': str(completion),
                       'context': row.get('context_length', 0)})
    return sorted(result, key=lambda row: (money(row['prompt']) + money(row['completion']), row['id']))


def picker_catalog(mode='default', *, cache=None):
    """UI-only cache. Billing/evaluation callers keep using live metadata()."""
    from .catalog_cache import CatalogCache, CatalogSnapshot, normalize_records
    cache = cache if cache is not None else CatalogCache()
    if mode not in ('default', 'refresh', 'cached'):
        raise ValueError('Unknown catalog selection mode.')
    if mode == 'cached':
        try:
            snapshot = cache.load(allow_stale=True)
        except (OSError, ValueError):
            raise ValueError('Cached catalog unavailable or invalid. Use /model refresh or /model EXACT_ID.') from None
        if snapshot is None:
            raise ValueError('No cached catalog. Use /model refresh or /model EXACT_ID.')
        return snapshot, ''
    if mode == 'default':
        try:
            snapshot = cache.load()
            if snapshot is not None:
                return snapshot, ''
        except (OSError, ValueError):
            pass  # Attempt a live refresh, never silently accept stale metadata.
    try:
        records = normalize_records(metadata('models'))
    except (OSError, ValueError, RecursionError):
        raise ValueError('Model catalog unavailable. Model unchanged. Use /model cached to explicitly review cached metadata, or /model EXACT_ID for manual selection. No inference requested.') from None
    fetched = time.time()
    try:
        return cache.save(records, fetched_at=fetched), ''
    except (OSError, ValueError):
        return CatalogSnapshot(records, fetched, False, 'live'), 'Catalog could not be cached; live metadata is available for this selection only.'
