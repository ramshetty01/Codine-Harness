"""Explicit user authentication commands; never available as model tools."""
import argparse
import getpass
from pathlib import Path
import sys

from .credentials import CredentialStore


def main(argv, *, store=None, input_key=getpass.getpass, login=None):
    parser = argparse.ArgumentParser(prog='codine auth', description='Manage provider API credentials; subscription logins are not supported.')
    parser.add_argument('action', choices=('status', 'login', 'set-key', 'logout'))
    parser.add_argument('provider', choices=('openrouter', 'nvidia', 'custom'), nargs='?', default='openrouter')
    parser.add_argument('--endpoint', help='Exact custom provider endpoint; required for custom credentials.')
    args = parser.parse_args(argv)
    store = store or CredentialStore()
    try:
        from .credentials import identity
        identity(args.provider, args.endpoint)
        if args.action == 'status':
            # Startup provenance without loading model configuration or contacting a provider.
            from .settings import Settings
            import os
            settings = Settings(Path.cwd(), dict(os.environ))
            if args.provider == 'custom':
                from .custom_endpoint import trusted
                key = trusted(settings, 'api_key', 'CODINE_CUSTOM_API_KEY', '')
            else:
                key = settings.get('api_key', 'OPENROUTER_API_KEY' if args.provider == 'openrouter' else 'NVIDIA_API_KEY', '')
            source = settings.sources['api_key']
            if source == 'default':
                key = store.get(args.provider, args.endpoint)
                source = 'user credential store' if key else 'not configured'
            print(f'{args.provider}: {"configured" if key else "not configured"}; source: {source}.')
            print('Offline presence check only; validity and balance were not checked. Existing running sessions use their startup credential.')
            return 0
        if args.action == 'logout':
            store.delete(args.provider, args.endpoint)
            print('Local saved credential removed. Remote API key remains valid; revoke it in your provider dashboard if needed.')
            print('Shell and .env credentials are unchanged. Restart running Codine processes to discard their in-memory key.')
            return 0
        if not sys.stdin.isatty():
            raise ValueError('Login/key entry requires an interactive terminal; secrets are not accepted through argv or piped input.')
        if args.action == 'login' and args.provider != 'openrouter':
            raise ValueError('Browser login is supported for OpenRouter only; use set-key for provider API credentials.')
        print(f'Credentials will be saved in {store.root}/keys.json: user-owned 0600 plaintext, NOT encrypted or stored in an OS keychain.')
        if args.action == 'login':
            print('Opening OpenRouter to authorize a new API key. This uses OpenRouter credits, not a ChatGPT/Claude subscription. Waiting up to 120 seconds; Ctrl+C cancels.')
            if login is None:
                from .openrouter_auth import login
            key = login()
        else:
            # Fail closed if getpass cannot hide input; never fall back to echo.
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter('error', getpass.GetPassWarning)
                key = input_key('Provider API key (hidden): ')
        if args.provider == 'custom':
            from .custom_endpoint import validate_key
            validate_key(key, tuple(value for value in (store.get('openrouter'), store.get('nvidia')) if value))
        store.set(args.provider, key, args.endpoint)
        print('Credential saved. Shell/.env keys still take precedence, including explicitly empty values. Restart Codine to load it.')
        return 0
    except KeyboardInterrupt:
        print('Authentication cancelled. A completed save is not rolled back; check auth status before retrying.', file=sys.stderr)
        return 130
    except Exception:
        # Neither provider bodies nor filesystem/JSON errors may expose secret data.
        print('Authentication action failed. Check provider/endpoint, private storage permissions, interactive input, or retry login. No secret details were printed.', file=sys.stderr)
        return 1
