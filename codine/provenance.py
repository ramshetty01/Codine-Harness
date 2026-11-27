"""Deterministic evaluation identities. Never serialize credentials or task answers."""
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform


def fingerprint(value):
    encoded = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def evaluation_manifest(tasks, *, mode, image=None, config=None, source_root=None):
    root = Path(source_root) if source_root else Path(__file__).parent
    # Deliberate code-only inventory: no .env, sessions, reports or project files.
    sources = {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
               for p in sorted(root.glob('*.py')) if not p.is_symlink()}
    settings = {'mode': mode}
    if config is not None:
        for key in ('provider', 'model', 'max_tokens', 'max_steps', 'context_chars',
                    'context_tokens', 'model_limits', 'router_max_price',
                    'budget_usd', 'request_budget_usd'):
            settings[key] = getattr(config, key, None)
    dependencies = {}
    for name in ('openai', 'python-dotenv', 'prompt-toolkit'):
        try:
            dependencies[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            dependencies[name] = None
    identities = {
        'source': {'files': sources, 'sha256': fingerprint(sources)},
        'tasks': [{'id': task['id'], 'sha256': fingerprint(task)} for task in tasks],
        'configuration': {'values': settings, 'sha256': fingerprint(settings)},
        'runtime': {'python': platform.python_version(), 'platform': platform.system(),
                    'machine': platform.machine(), 'dependencies': dependencies, 'image': image},
    }
    return {'version': 1, **identities, 'sha256': fingerprint(identities)}
