"""Private fixed-destination exchange subprocess; never invoke for inference."""
import json
import sys
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler
EXCHANGE_URL = 'https://openrouter.ai/api/v1/auth/keys'


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result: raise ValueError('Duplicate field')
        result[key] = value
    return result


def validate_key(key):
    if not isinstance(key, str) or not 8 <= len(key) <= 4096 or any(ord(c) < 33 or ord(c) > 126 for c in key):
        raise ValueError('Invalid key')


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('Authentication redirects are not accepted.')


def exchange(code, verifier):
    """Fixed HTTPS destination; no ambient proxy, cookies, auth headers or retries."""
    request = Request(EXCHANGE_URL, data=json.dumps({'code': code, 'code_verifier': verifier,
                      'code_challenge_method': 'S256'}).encode(),
                      headers={'Content-Type': 'application/json', 'Accept': 'application/json'}, method='POST')
    try:
        with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=15) as response:
            if response.status != 200: raise ValueError('Authentication exchange failed.')
            raw = response.read(16385)
            if len(raw) > 16384: raise ValueError('Authentication response exceeds limit.')
            result = json.loads(raw, object_pairs_hook=_pairs)
            if not isinstance(result, dict): raise ValueError('Invalid authentication response.')
            key = result.get('key')
            validate_key(key)
            return key
    except Exception:
        # Provider bodies, URLs and parser exception details may contain secrets.
        raise ValueError('OpenRouter authentication exchange failed; no credential was saved. Restart login or use set-key.') from None


if __name__ == '__main__':
    try:
        raw = sys.stdin.buffer.read(8193)
        if len(raw) > 8192: raise ValueError('Input too large')
        data = json.loads(raw, object_pairs_hook=_pairs)
        key = exchange(data['code'], data['verifier'])
        sys.stdout.write(json.dumps({'key': key}))
    except BaseException:
        sys.exit(1)
