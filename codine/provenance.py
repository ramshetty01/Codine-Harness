"""Deterministic evaluation identities. Never serialize credentials or task answers."""
import ast
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform


def fingerprint(value):
    encoded = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def evaluation_manifest(tasks, *, mode, image=None, config=None, source_root=None, request_settings=None, execution_settings=None):
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
    for name in ('openai', 'httpx2', 'python-dotenv', 'prompt-toolkit'):
        try:
            dependencies[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            dependencies[name] = None
    # Grading/executor implementation is a controlled condition, not a harness
    # revision variable. AST slices let unrelated CLI/report changes differ.
    grading = {}
    for name, symbols in {'evals.py': {'GRADER','equivalent','task_cases','grade'},
                          'command_evals.py': {'validate_pack','run_task'}}.items():
        path=root/name
        if path.is_file() and not path.is_symlink():
            selected=[]
            for node in ast.parse(path.read_text()).body:
                names={target.id for target in node.targets if isinstance(target,ast.Name)} if isinstance(node,ast.Assign) else {getattr(node,'name','')}
                if names & symbols:selected.append(ast.dump(node,include_attributes=False))
            grading[name]=fingerprint(selected)
    for name in ('sandbox.py','repository_executor.py','staging.py'):
        if name in sources:grading[name]=sources[name]
    controls={'grading':grading,'request_settings':request_settings,
              'execution_settings':execution_settings or {}}
    identities = {
        'source': {'files': sources, 'sha256': fingerprint(sources)},
        'tasks': [{'id': task['id'], 'sha256': fingerprint(task)} for task in tasks],
        'configuration': {'values': settings, 'sha256': fingerprint(settings)},
        'controls': {'values': controls, 'sha256': fingerprint(controls)},
        'runtime': {'python': platform.python_version(), 'platform': platform.system(),
                    'machine': platform.machine(), 'dependencies': dependencies, 'image': image},
    }
    return {'version': 2, **identities, 'sha256': fingerprint(identities)}
