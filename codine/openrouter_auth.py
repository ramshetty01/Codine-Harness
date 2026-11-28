"""Explicit OpenRouter PKCE login. No existing credential is read or transmitted."""
import base64
import hashlib
import hmac
import json
import secrets
import socket
import time
from urllib.parse import urlencode, urlsplit, parse_qs
import webbrowser

from .credentials import validate_key, _pairs

AUTH_URL = 'https://openrouter.ai/auth'
EXCHANGE_URL = 'https://openrouter.ai/api/v1/auth/keys'


def exchange(code, verifier):
    # A dedicated child gives DNS, TLS, headers and slow-drip responses one hard
    # wall deadline. Secrets travel over stdin/stdout pipes, never process argv.
    from pathlib import Path
    import subprocess
    import sys
    try:
        process = subprocess.Popen([sys.executable, '-I', str(Path(__file__).with_name('auth_exchange.py'))],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env={'LANG': 'C.UTF-8'}, start_new_session=True)
        try:
            output, _ = process.communicate(json.dumps({'code': code, 'verifier': verifier}).encode(), timeout=20)
        except BaseException:
            process.kill(); process.communicate()
            raise
        if process.returncode or len(output) > 8192: raise ValueError('Exchange failed.')
        result = json.loads(output, object_pairs_hook=_pairs)
        key = result.get('key')
        validate_key(key)
        return key
    except KeyboardInterrupt:
        raise
    except Exception:
        raise ValueError('OpenRouter authentication exchange failed; no credential was saved. Restart login or use set-key.') from None


class Callback:
    def __init__(self, timeout=120):
        if not 0 < timeout <= 120: raise ValueError('Callback timeout must be between 0 and 120 seconds.')
        self.deadline = time.monotonic() + timeout
        self.state = secrets.token_urlsafe(32)
        self.path = '/callback/' + secrets.token_urlsafe(24)
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self.listener.bind(('127.0.0.1', 0)); self.listener.listen(4)
        except BaseException:
            self.listener.close(); raise
        self.host = '127.0.0.1:' + str(self.listener.getsockname()[1])
        self.url = 'http://' + self.host + self.path

    def close(self): self.listener.close()

    def parse(self, raw):
        if len(raw) > 8192 or not raw.endswith(b'\r\n\r\n'): return None
        try:
            lines = raw.decode('ascii').split('\r\n')
            method, target, version = lines[0].split(' ')
            if method != 'GET' or version not in ('HTTP/1.0', 'HTTP/1.1') or len(lines) > 35: return None
            fields = {}
            for line in lines[1:-2]:
                name, value = line.split(':', 1)
                name = name.lower()
                if name in fields or name.strip() != name: return None
                fields[name] = value.strip()
            if fields.get('host') != self.host or 'transfer-encoding' in fields or fields.get('content-length', '0') != '0': return None
            url = urlsplit(target)
            if url.scheme or url.netloc or url.fragment or url.path != self.path: return None
            query = parse_qs(url.query, keep_blank_values=True, strict_parsing=True, max_num_fields=6)
            if set(query) != {'code', 'state'} or any(len(v) != 1 for v in query.values()): return None
            state = query['state'][0]
            if not state.isascii() or not hmac.compare_digest(state, self.state): return None
            code = query['code'][0]
            if not 1 <= len(code) <= 2048 or any(ord(c) < 33 or ord(c) > 126 for c in code): return None
            return code
        except (ValueError, UnicodeError): return None

    def wait(self):
        while time.monotonic() < self.deadline:
            self.listener.settimeout(min(.25, max(.001, self.deadline - time.monotonic())))
            try: connection, address = self.listener.accept()
            except socket.timeout: continue
            with connection:
                connection.settimeout(min(1, max(.001, self.deadline - time.monotonic())))
                raw = b''; code = None
                try:
                    end = min(self.deadline, time.monotonic() + 1)
                    while b'\r\n\r\n' not in raw and len(raw) <= 8192 and time.monotonic() < end:
                        connection.settimeout(max(.001, end - time.monotonic()))
                        part = connection.recv(min(1024, 8193-len(raw)))
                        if not part: break
                        raw += part
                    if time.monotonic() < self.deadline and address[0] == '127.0.0.1': code = self.parse(raw)
                    message = b'Return to Codine to finish login.' if code else b'Invalid authentication callback.'
                    status = b'200 OK' if code else b'400 Bad Request'
                    connection.sendall(b'HTTP/1.1 ' + status + b'\r\nContent-Type: text/plain\r\nCache-Control: no-store\r\nReferrer-Policy: no-referrer\r\nContent-Security-Policy: default-src \'none\'\r\nConnection: close\r\nContent-Length: ' + str(len(message)).encode() + b'\r\n\r\n' + message)
                except OSError: pass
                if code: return code
        raise ValueError('Login timed out; no credential was saved.')


def login(*, open_browser=webbrowser.open, exchange_code=exchange, timeout=120):
    verifier = secrets.token_urlsafe(32)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
    callback = Callback(timeout)
    try:
        url = AUTH_URL + '?' + urlencode({'callback_url': callback.url, 'state': callback.state,
                    'code_challenge': challenge, 'code_challenge_method': 'S256', 'key_label': 'Codine'})
        if not open_browser(url): raise ValueError('Could not open a browser; use codine auth set-key openrouter instead.')
        code = callback.wait()
        return exchange_code(code, verifier)
    finally: callback.close()
