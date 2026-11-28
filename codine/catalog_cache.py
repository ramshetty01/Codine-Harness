"""Private, endpoint-scoped public model metadata. Never a billing authority."""
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import tempfile
import time

from .paths import STATE_ROOT

MODELS_ENDPOINT = 'https://openrouter.ai/api/v1/models'
TTL_SECONDS = 3600
MAX_BYTES = 8_000_000
MAX_MODELS = 10000


@dataclass(frozen=True)
class CatalogSnapshot:
    records: list
    fetched_at: float
    stale: bool
    source: str = 'cache'

    def describe(self):
        age = max(0, int(time.time() - self.fetched_at))
        return (f'Model catalog: {self.source}, age {age}s, ' +
                ('STALE (explicit selection only)' if self.stale else 'fresh (TTL 3600s)') +
                '. Advertised metadata is not a billing guarantee.')


def normalize_records(records):
    if not isinstance(records, list) or len(records) > MAX_MODELS:
        raise ValueError('Invalid model catalog count.')
    output, seen = [], set()
    for row in records:
        if not isinstance(row, dict):
            raise ValueError('Invalid model catalog entry.')
        name = row.get('id')
        if not isinstance(name, str) or not name or len(name) > 200 or any(not c.isprintable() or c.isspace() for c in name) or name in seen:
            raise ValueError('Invalid or duplicate model ID.')
        seen.add(name)
        item = {'id': name}
        if isinstance(row.get('name'), str):
            item['name'] = row['name'][:300]
        parameters = row.get('supported_parameters')
        if parameters is not None:
            if not isinstance(parameters, list) or len(parameters) > 100 or any(not isinstance(p, str) or len(p) > 100 for p in parameters):
                raise ValueError('Invalid supported parameters.')
            item['supported_parameters'] = parameters
        for field in ('context_length',):
            if row.get(field) is not None:
                if type(row[field]) is not int or not 1 <= row[field] <= 100_000_000:
                    raise ValueError('Invalid catalog token limit.')
                item[field] = row[field]
        top = row.get('top_provider')
        if isinstance(top, dict) and top.get('max_completion_tokens') is not None:
            limit = top['max_completion_tokens']
            if type(limit) is not int or not 1 <= limit <= 100_000_000:
                raise ValueError('Invalid catalog output limit.')
            item['top_provider'] = {'max_completion_tokens': limit}
        architecture = row.get('architecture')
        if architecture is not None:
            if not isinstance(architecture, dict):
                raise ValueError('Invalid catalog architecture.')
            item['architecture'] = {}
            for field in ('input_modalities', 'output_modalities'):
                if field in architecture:
                    modes = architecture[field]
                    if not isinstance(modes, list) or len(modes) > 20 or any(not isinstance(p, str) or len(p) > 100 for p in modes):
                        raise ValueError('Invalid model modalities.')
                    item['architecture'][field] = modes
        pricing = row.get('pricing')
        if pricing is not None:
            if not isinstance(pricing, dict) or len(pricing) > 100:
                raise ValueError('Invalid catalog pricing.')
            # Preserve unknown price fields: never fill missing prices with zero.
            if any(not isinstance(k, str) or len(k) > 100 or not isinstance(v, (str, int, float)) or isinstance(v, bool) or len(str(v)) > 100 for k, v in pricing.items()):
                raise ValueError('Invalid catalog pricing values.')
            item['pricing'] = pricing
        output.append(item)
    return output


class CatalogCache:
    def __init__(self, root=None, *, provider='openrouter', endpoint=MODELS_ENDPOINT):
        self.root = Path(root) if root is not None else STATE_ROOT / 'catalogs'
        self.provider, self.endpoint = provider, endpoint
        if not isinstance(provider, str) or not isinstance(endpoint, str) or len(provider) > 100 or len(endpoint) > 1000:
            raise ValueError('Invalid catalog scope.')
        self.path = self.root / (hashlib.sha256((provider + '\n' + endpoint).encode()).hexdigest() + '.json')

    def _directory(self, create=False):
        # Do not follow a user-state directory replacement to an attacker path.
        for parent in (self.root, *self.root.parents):
            if parent.is_symlink():
                raise ValueError('Catalog cache directory must not be a symlink.')
        if create:
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.root.exists():
            info = self.root.stat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise ValueError('Catalog cache directory must be user-owned and private (0700).')

    def load(self, *, allow_stale=False):
        self._directory()
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            return None
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1 or info.st_mode & 0o077 or info.st_size > MAX_BYTES:
                raise ValueError('Invalid private catalog cache file.')
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError('Catalog cache too large.')
        try:
            def unique(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError('Duplicate catalog cache field.')
                    result[key] = value
                return result
            document = json.loads(raw, object_pairs_hook=unique)
            if not isinstance(document, dict) or set(document) != {'version', 'provider', 'endpoint', 'fetched_at', 'records'} or type(document['version']) is not int or document['version'] != 1:
                raise ValueError('Unsupported catalog cache schema.')
            if document['provider'] != self.provider or document['endpoint'] != self.endpoint:
                raise ValueError('Catalog endpoint scope mismatch.')
            fetched = document['fetched_at']
            if type(fetched) not in (float, int) or not math.isfinite(fetched) or fetched < 0 or fetched > time.time() + 60:
                raise ValueError('Invalid catalog timestamp.')
            stale = time.time() - fetched >= TTL_SECONDS
            if stale and not allow_stale:
                raise ValueError('Cached model catalog is stale.')
            return CatalogSnapshot(normalize_records(document['records']), fetched, stale)
        except (TypeError, KeyError, RecursionError, UnicodeError) as error:
            raise ValueError('Malformed catalog cache.') from error

    def model_record(self, model_id, *, allow_stale=False):
        snapshot = self.load(allow_stale=allow_stale)
        return next((row for row in snapshot.records if row['id'] == model_id), None) if snapshot else None

    def save(self, records, *, fetched_at=None):
        records = normalize_records(records)
        fetched = time.time() if fetched_at is None else fetched_at
        if type(fetched) not in (int, float) or not math.isfinite(fetched) or fetched < 0 or fetched > time.time() + 60:
            raise ValueError('Invalid catalog timestamp.')
        raw = json.dumps({'version': 1, 'provider': self.provider, 'endpoint': self.endpoint,
                          'fetched_at': fetched, 'records': records}, allow_nan=False).encode()
        if len(raw) > MAX_BYTES:
            raise ValueError('Catalog cache too large.')
        self._directory(create=True)
        fd, temporary = tempfile.mkstemp(prefix='.catalog-', dir=self.root)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return CatalogSnapshot(records, fetched, time.time() - fetched >= TTL_SECONDS, 'live')
