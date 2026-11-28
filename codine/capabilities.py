"""Local provider request planning. Never fetch metadata or run inference here."""
from dataclasses import dataclass
import math
import os
from pathlib import Path
import re

# Captured before Config.load loads dotenv; deliberately independent of checkout
# CONFIG_ROOT, project configuration, and any project-provided environment path.
USER_CAPABILITIES = Path.home() / '.config' / 'codine' / 'model-capabilities.json'
OPTIONAL = {'temperature', 'top_p', 'seed', 'parallel_tool_calls', 'reasoning', 'thinking'}


class CapabilityError(ValueError):
    pass


@dataclass(frozen=True)
class RequestPlan:
    kwargs: dict
    source: str
    supported_parameters: frozenset | None
    omitted: tuple = ()
    endpoint: str | None = None
    provider: str = 'openrouter'

    @property
    def note(self):
        support = ('unknown locally; required parameters enforced by OpenRouter routing' if self.provider == 'openrouter' else 'unknown direct-endpoint support; tools blocked') if self.supported_parameters is None else self.source
        omitted = f'; omitted unsupported optional parameters: {", ".join(self.omitted)}' if self.omitted else ''
        return f'{self.provider} capabilities: {support}{omitted}.'


def _parameters(value):
    if not isinstance(value, list) or any(not isinstance(item, str) or not item or len(item) > 100 for item in value):
        raise CapabilityError('supported_parameters must be a list of parameter names.')
    return frozenset(value)


def _options(value):
    if not isinstance(value, dict) or set(value) - OPTIONAL:
        raise CapabilityError('Unsupported optional parameter declaration.')
    for name, option in value.items():
        if name in ('temperature', 'top_p'):
            ceiling = 2 if name == 'temperature' else 1
            valid = type(option) in (int, float) and math.isfinite(option) and 0 <= option <= ceiling
        elif name == 'seed':
            valid = type(option) is int and 0 <= option <= 2**31 - 1
        elif name == 'parallel_tool_calls':
            valid = type(option) is bool
        elif name == 'thinking':
            valid = isinstance(option, dict) and set(option) == {'enabled'} and type(option['enabled']) is bool
        else:
            valid = (isinstance(option, dict) and set(option) == {'effort'} and
                     option['effort'] in ('none', 'minimal', 'low', 'medium', 'high', 'xhigh'))
        if not valid:
            raise CapabilityError(f'Invalid declared optional parameter: {name}.')
    return value


def declarations(project, source=USER_CAPABILITIES):
    """Only explicit user-owned declarations; never project-local configuration."""
    source = Path(source).absolute()
    if not os.path.lexists(source):
        return {}
    if source.resolve().is_relative_to(Path(project).resolve()):
        raise CapabilityError('Model capability declarations must be outside the project.')
    from .execution_profiles import _load_private_source
    try:
        data = _load_private_source(source)
    except ValueError:
        raise CapabilityError('Cannot safely read user model capability declarations.') from None
    if (not isinstance(data, dict) or set(data) - {'version', 'openrouter', 'nvidia', 'custom'} or
            type(data.get('version')) is not int or data['version'] != 1):
        raise CapabilityError('Model capability declarations require version 1 and provider objects.')
    for provider in ('openrouter', 'nvidia', 'custom'):
        entries = data.get(provider, {})
        if not isinstance(entries, dict):
            raise CapabilityError('Provider declarations must be objects.')
        for model, spec in entries.items():
            if not isinstance(model, str) or not model or not isinstance(spec, dict):
                raise CapabilityError('Invalid exact-model capability declaration.')
            if set(spec) - {'supported_parameters', 'parameters', 'endpoint', 'max_output_tokens'}:
                raise CapabilityError('Unknown model capability declaration field.')
            if 'supported_parameters' in spec:
                _parameters(spec['supported_parameters'])
            _options(spec.get('parameters', {}))
            if 'endpoint' in spec:
                endpoint = spec['endpoint']
                from .config import BASE_URL
                valid = (isinstance(endpoint, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_./-]{0,127}', endpoint)) if provider == 'openrouter' else endpoint == BASE_URL
                if provider == 'custom':
                    from .custom_endpoint import canonical_endpoint
                    valid = canonical_endpoint(endpoint) == endpoint
                if not valid:
                    raise CapabilityError('Invalid provider endpoint scope.')
            if 'max_output_tokens' in spec and (type(spec['max_output_tokens']) is not int or not 1 <= spec['max_output_tokens'] <= 10_000_000):
                raise CapabilityError('max_output_tokens must be a positive integer.')
    return {provider: data.get(provider, {}) for provider in ('openrouter', 'nvidia', 'custom')}


def cached_record(model):
    # M3 owns cache schema, TTL, trust checks and refresh. There is no fetch here.
    from .catalog_cache import CatalogCache
    try:
        return CatalogCache().model_record(model, allow_stale=False)
    except (OSError, ValueError):
        return None  # stale/corrupt metadata is not evidence of model support


def plan_request(config, model, schemas, *, record=None, declared=None, provider='openrouter'):
    """Pure request adaptation; declarations are supplied only by trusted caller."""
    from .config import BASE_URL, DEFAULT_MODEL
    builtin = provider == 'nvidia' and model == DEFAULT_MODEL and declared is None
    if builtin:
        declared = {'supported_parameters': ['tools', 'temperature', 'top_p', 'seed', 'parallel_tool_calls', 'thinking'],
                    'parameters': {'temperature': 1, 'top_p': .95, 'seed': 42,
                                   'parallel_tool_calls': True, 'thinking': {'enabled': True}}}
    declared = declared or {}
    supported, source = None, 'unknown'
    if isinstance(record, dict) and record.get('id') == model and record.get('supported_parameters') is not None:
        supported, source = _parameters(record['supported_parameters']), 'fresh model catalog (not endpoint verification)'
    endpoint = declared.get('endpoint')
    if endpoint:
        # Catalog capability union cannot prove support by a selected endpoint.
        supported, source = None, 'unknown endpoint'
    if 'supported_parameters' in declared:
        supported, source = _parameters(declared['supported_parameters']), 'user declaration (not provider verification)'
    if builtin:
        source = 'built-in compatibility profile for configured NVIDIA model (not live verification)'
    if schemas and ((supported is not None and 'tools' not in supported) or (supported is None and provider != 'openrouter')):
        raise CapabilityError('Selected model/endpoint does not declare tool support. Choose a tool-capable model; no request sent.')
    output = config.request_limits(model)[1]
    limit = declared.get('max_output_tokens')
    if limit is None and isinstance(record, dict) and record.get('id') == model:
        top = record.get('top_provider')
        limit = top.get('max_completion_tokens') if isinstance(top, dict) else None
    if type(limit) is int and limit > 0:
        output = min(output, limit)
    routing = {'require_parameters': True, 'allow_fallbacks': False}
    if config.router_max_price:
        routing['max_price'] = dict(config.router_max_price)
    if endpoint:
        if provider == 'openrouter':
            routing.update(only=[endpoint], allow_fallbacks=False)
        elif provider == 'custom':
            if endpoint != config.custom_endpoint:
                raise CapabilityError('Custom capability declaration does not match the configured endpoint.')
        elif endpoint != BASE_URL:
            raise CapabilityError('NVIDIA declaration endpoint does not match the configured transport.')
    if provider == 'custom' and declared and endpoint != config.custom_endpoint:
        raise CapabilityError('Custom declarations require an exact endpoint binding.')
    kwargs = {'max_tokens': output, 'stream': True, 'extra_body': {'provider': routing} if provider == 'openrouter' else {}}
    if provider == 'nvidia':
        kwargs['stream_options'] = {'include_usage': True}
    if schemas:
        kwargs['tools'] = schemas
    omitted = []
    for name, value in _options(declared.get('parameters', {})).items():
        if supported is None or name not in supported or (name == 'parallel_tool_calls' and not schemas) or (name == 'thinking' and provider != 'nvidia') or (name == 'reasoning' and provider != 'openrouter'):
            omitted.append(name)
        elif name == 'thinking':
            kwargs['extra_body']['chat_template_kwargs'] = {'enable_thinking': value['enabled']}
        elif name == 'reasoning':
            kwargs['extra_body']['reasoning'] = dict(value)
        else:
            kwargs[name] = value
    return RequestPlan(kwargs, source, supported, tuple(sorted(omitted)), endpoint, provider)
