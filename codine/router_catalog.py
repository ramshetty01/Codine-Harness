"""Bounded OpenRouter metadata reads. These endpoints do not run inference."""
import json
import urllib.error
import urllib.request
from .budget import money


def metadata(endpoint, api_key=''):
    if endpoint not in ('models', 'key', 'credits'):
        raise ValueError('Unsupported metadata endpoint.')
    headers = {'Accept': 'application/json'}
    if api_key:
        headers['Authorization'] = 'Bearer ' + api_key
    request = urllib.request.Request('https://openrouter.ai/api/v1/' + endpoint, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read(8_000_001)
        if len(raw) > 8_000_000:
            raise ValueError('Catalog response exceeds local limit.')
        return json.loads(raw)['data']
    except (urllib.error.URLError, KeyError, ValueError) as error:
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
