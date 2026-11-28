"""Trusted JSONL bridge launched with -I; extension Python is NOT sandboxed."""
import importlib.util
import json
from pathlib import Path
import sys

LIMIT = 65536


def main():
    # -I excludes ambient cwd/PYTHONPATH. Only the installed SDK and reviewed
    # staged package are deliberately added. Host privileges remain unrestricted.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from codine.extension_api import ExtensionAPI
    entrypoint = Path(sys.argv[1])
    package = Path(sys.argv[2])
    capabilities = json.loads(sys.argv[3])
    sys.path.insert(0, str(package))
    output = sys.stdout
    sys.stdout = sys.stderr
    api = None
    for raw in iter(lambda: sys.stdin.buffer.readline(LIMIT + 1), b''):
        request_id = None
        try:
            if len(raw) > LIMIT or not raw.endswith(b'\n'):
                raise ValueError('Oversized or incomplete extension request.')
            request = json.loads(raw)
            if (not isinstance(request, dict) or request.get('version') != 1 or
                    type(request.get('id')) is not int or request['id'] < 1):
                raise ValueError('Invalid extension protocol request.')
            request_id = request['id']
            if request.get('operation') == 'describe':
                if api is None:
                    api = ExtensionAPI(capabilities)
                    spec = importlib.util.spec_from_file_location('_codine_extension', entrypoint)
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                    module.register(api)
                result = api.describe()
            elif request.get('operation') == 'invoke' and api is not None:
                result = api.invoke(request['kind'], request['name'], request.get('payload'))
            else:
                raise ValueError('Unknown extension operation or unregistered extension.')
            reply = {'version': 1, 'id': request_id, 'ok': True, 'result': result}
            encoded = json.dumps(reply, ensure_ascii=True, allow_nan=False).encode() + b'\n'
            if len(encoded) > LIMIT: raise ValueError('Extension response exceeds protocol limit.')
        except Exception as error:
            try: message = str(error)[:2000]
            except Exception: message = 'Extension handler failed.'
            encoded = json.dumps({'version': 1, 'id': request_id, 'ok': False,
                                  'error': type(error).__name__ + ': ' + message}, ensure_ascii=True).encode() + b'\n'
        output.buffer.write(encoded)
        output.buffer.flush()


if __name__ == '__main__':
    main()
