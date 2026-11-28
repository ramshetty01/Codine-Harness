"""Idle terminal extension controls; discovery reads metadata, never imports code."""
import json

from .extension_manifest import parse_manifest, read_bytes
from .repository_search import list_files

MAX_CANDIDATES = 200
MAX_RESULTS = 32
MAX_OUTPUT_BYTES = 24000
USAGE = ('Usage: /extensions [list | discover [DIRECTORY] | load MANIFEST | '
         'unload NAME | reload NAME | panel NAME:PANEL]; /ext NAME:COMMAND [ARGUMENT]')


class _DiscoveryWorkspace:
    """Read-only policy facade: scanning ignore files cannot ask for approval."""
    def __init__(self, workspace):
        self.workspace = workspace
        self.root = workspace.root

    def resolve(self, name, directory=False):
        target = self.workspace.resolve(name, directory=directory)
        decision = self.workspace._file_decision('read', target)
        if decision and decision.action != 'allow':
            raise ValueError('Extension discovery needs read permission; no approval requested.')
        return target

    def _read(self, target):
        relative = target.relative_to(self.root).as_posix()
        self.resolve(relative)
        data, _ = read_bytes(self.root, relative, 16384)
        return data, data.decode('utf-8')


def discover(workspace, path='.'):
    """Bounded JSON manifest search; no entrypoint/resource bytes are read."""
    view = _DiscoveryWorkspace(workspace)
    try:
        inventory = list_files(view, path, recursive=True, limit=MAX_CANDIDATES,
                               glob='*.json', respect_ignore=True)
    except (OSError, ValueError):
        return {'extensions': [], 'truncated': True, 'reason': 'scope_or_ignore_metadata_unavailable',
                'notice': 'Discovery requires permitted directory and ignore-file reads; narrow the scope or change user policy.'}
    rows = []
    truncated = inventory['truncated']
    reason = inventory.get('reason')
    for name in inventory['files']:
        try:
            view.resolve(name)
            raw, _ = read_bytes(workspace.root, name, 65536)
            manifest = parse_manifest(raw)
        except (OSError, ValueError):
            continue  # Ordinary JSON, unavailable files and denied metadata are not disclosed.
        row = {'manifest_path': name, 'name': manifest['name'], 'entrypoint': manifest['entrypoint'],
               'capabilities': manifest['capabilities'], 'declared_files': len(manifest['files']),
               'trust_required': True}
        if len(rows) >= MAX_RESULTS or len(json.dumps(rows + [row]).encode()) > MAX_OUTPUT_BYTES:
            truncated = True; reason = 'metadata_result_limit'; break
        rows.append(row)
    return {'extensions': rows, 'truncated': truncated, 'reason': reason,
            'notice': 'Metadata only; source existence and trust are not verified. Use /trust review MANIFEST, inspect source, then /trust allow DIGEST before /extensions load MANIFEST.'}


def command(kind, argument, agent, workspace, ui):
    if getattr(agent, '_in_turn', False):
        raise ValueError('Extension commands run only at the idle prompt; submit them to the command queue while busy.')
    argument = argument.strip()
    if kind == 'extensions':
        action, _, value = argument.partition(' ')
        action = action or 'list'
        value = value.strip()
        if action == 'discover':
            result = discover(workspace, value or '.')
            ui.write(json.dumps(result, ensure_ascii=False, indent=2))
            return result
        if action not in ('list', 'load', 'unload', 'reload', 'panel') or (action == 'list' and value) or (action != 'list' and not value):
            raise ValueError(USAGE)
    elif kind == 'ext':
        action = 'invoke_command'
        value, _, payload = argument.partition(' ')
        if ':' not in value or any(not part for part in value.split(':')) or value.count(':') != 1:
            raise ValueError(USAGE)
    else:
        raise ValueError(USAGE)
    manager = getattr(agent, 'extension_manager', None)
    if manager is None:
        raise ValueError('Extension runtime is unavailable in this session; discovery and trust review remain available.')
    if action == 'list':
        result = manager.list()
    elif action == 'invoke_command':
        result = manager.invoke_command(value, payload)
    elif action == 'panel':
        if value.count(':') != 1 or any(not part for part in value.split(':')):
            raise ValueError(USAGE)
        result = manager.panel(value)
    else:
        result = getattr(manager, action)(value)
    ui.write(result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, indent=2))
    return result
