"""Atomic runtime registration of explicitly trusted extension snapshots."""
import hashlib
import re

from .extension_api import ExtensionAPI
from .extension_manifest import snapshot_extension
from .extension_process import ExtensionProcess
from .registry import ToolSpec
from .terminal import safe_text


class ExtensionManager:
    def __init__(self, workspace, registry, trust_store, redactor, cancel_event=None):
        self.workspace, self.registry = workspace, registry
        self.trust_store, self.redactor = trust_store, redactor
        self.cancel_event = cancel_event
        self.loaded = {}

    def _execution_allowed(self):
        if self.workspace.mode == 'plan':
            raise ValueError('Extensions execute trusted Python and are disabled in plan mode.')

    def _cancel(self):
        event = getattr(self.workspace, 'cancel_event', self.cancel_event)
        if event is not None and event.is_set():
            raise KeyboardInterrupt
        return event

    @staticmethod
    def _descriptors(snapshot, description):
        if not isinstance(description, dict) or set(description) != {'version', 'registrations'}:
            raise ValueError('Invalid extension registration envelope.')
        api = ExtensionAPI(snapshot['manifest']['capabilities'], version=description['version'])
        rows = description['registrations']
        if not isinstance(rows, dict) or set(rows) != {'tools', 'commands', 'events', 'ui'}:
            raise ValueError('Invalid extension registration categories.')
        for kind, entries in rows.items():
            if not isinstance(entries, list): raise ValueError('Invalid extension registration list.')
            for row in entries:
                fields = {'name', 'description', 'parameters'} if kind == 'tools' else {'name', 'description'} if kind == 'commands' else {'name'}
                if not isinstance(row, dict) or set(row) != fields:
                    raise ValueError('Invalid extension registration fields.')
                if kind == 'tools': api.tool(row['name'], row['description'], row['parameters'], lambda _: None)
                elif kind == 'commands': api.command(row['name'], row['description'], lambda _: None)
                elif kind == 'events': api.on(row['name'], lambda _: None)
                else: api.panel(row['name'], lambda: '')
        return api.describe()['registrations']

    def _install(self, path, replacing=None):
        self._execution_allowed()
        self._cancel()
        if replacing is None and len(self.loaded) >= 8:
            raise ValueError('At most eight extensions may be loaded.')
        snapshot = snapshot_extension(self.workspace, path)
        name = snapshot['manifest']['name']
        if replacing is not None and name != replacing:
            raise ValueError('Reload cannot rename an extension; unload and load explicitly.')
        if name in self.loaded and replacing != name:
            raise ValueError('Extension is already loaded; use reload explicitly.')
        worker = ExtensionProcess(snapshot, self.workspace.root, self.trust_store, self.redactor, self._cancel())
        try:
            descriptors = self._descriptors(snapshot, worker.describe())
            old = self.loaded.get(name)
            old_names = set(old['tools']) if old else set()
            prefix = 'ext_' + re.sub('[^a-z0-9_]', '_', name.lower())[:20] + '_' + hashlib.sha256(name.encode()).hexdigest()[:8] + '_'
            specs = {}
            availability = lambda: self._available(name)
            for row in descriptors['tools']:
                full = prefix + row['name']
                if full in self.registry.tools and full not in old_names:
                    raise ValueError('Extension tool name collides with an existing registration.')
                specs[full] = ToolSpec(full, safe_text(row['description']), row['parameters'], self._tool_handler(name, row['name']), effect='execute', deferred=True,
                                       availability=availability)
            if sum(len(item['tools']) for key, item in self.loaded.items() if key != name) + len(specs) > 64:
                raise ValueError('At most 64 extension tools may be registered.')
            self._cancel()
            if not self.trust_store.trusted(snapshot):
                raise ValueError('Extension trust was revoked during registration.')
            # No mutation before validation succeeds; idle prompt is the lifecycle boundary.
            updated = {key: value for key, value in self.registry.tools.items() if key not in old_names}
            updated.update(specs)
            self.registry.tools = updated
            self.loaded[name] = {'worker': worker, 'snapshot': snapshot, 'descriptors': descriptors, 'tools': list(specs)}
        except BaseException:
            worker.close()
            raise
        if old: old['worker'].close()
        return self._row(name)

    def _available(self, name):
        entry = self.loaded.get(name)
        return bool(entry and not entry['worker'].closed and self.trust_store.trusted(entry['snapshot']))

    def _tool_handler(self, name, local):
        def invoke(**args):
            self._execution_allowed()
            self._cancel()
            if not self.workspace.approve('Run trusted extension tool ' + name + ':' + local + '?\nThis Python code has host-user privileges, not sandbox isolation.\nArguments: ' + safe_text(self.redactor.text(str(args)))):
                return {'status': 'declined'}
            return self._invoke(name, 'tools', local, args)
        return invoke

    def load(self, manifest_path):
        return self._install(manifest_path)

    def reload(self, name):
        entry = self.loaded.get(name)
        if entry is None: raise ValueError('Extension is not loaded.')
        return self._install(entry['snapshot']['manifest_path'], replacing=name)

    def _row(self, name):
        item = self.loaded[name]
        return {'name': name, 'digest': item['snapshot']['digest'],
                'manifest_path': item['snapshot']['manifest_path'],
                'status': 'disabled' if item['worker'].closed else 'loaded',
                'tools': list(item['tools']),
                **{kind: [row['name'] for row in item['descriptors'][kind]] for kind in ('commands', 'events', 'ui')}}

    def list(self):
        return [self._row(name) for name in sorted(self.loaded)]

    def unload(self, name):
        item = self.loaded.get(name)
        if item is None: raise ValueError('Extension is not loaded.')
        item['worker'].close()
        self.registry.tools = {key: value for key, value in self.registry.tools.items() if key not in item['tools']}
        del self.loaded[name]
        return {'name': name, 'status': 'unloaded'}

    def _invoke(self, name, kind, local, payload=None):
        self._execution_allowed()
        entry = self.loaded.get(name)
        if entry is None: raise ValueError('Extension is not loaded.')
        entry['worker'].cancel_event = self._cancel()
        return entry['worker'].invoke(kind, local, payload)

    def _selector(self, selector, kind, payload=None):
        if not isinstance(selector, str) or selector.count(':') != 1:
            raise ValueError('Use EXTENSION:NAME.')
        name, local = selector.split(':')
        return self._invoke(name, kind, local, payload)

    def invoke_command(self, selector, argument):
        return self._selector(selector, 'commands', argument)

    def panel(self, selector):
        result = self._selector(selector, 'ui')
        if not isinstance(result, str): raise ValueError('Extension panel must return text.')
        return safe_text(self.redactor.text(result))

    def turn_end(self, record):
        """Run only after a successful durable turn; failures never rewrite its outcome."""
        if self.workspace.mode == 'plan': return []
        errors = []
        for name, entry in list(self.loaded.items()):
            if entry['worker'].closed or not entry['descriptors']['events']: continue
            try:
                self._invoke(name, 'events', 'turn_end', record)
            except (Exception, KeyboardInterrupt) as error:
                try:
                    entry['worker'].close()
                except Exception:
                    pass  # Report the original callback failure, without rewriting the saved turn.
                errors.append('Extension ' + name + ' disabled after event failure: ' + safe_text(self.redactor.text(str(error))))
                if isinstance(error, KeyboardInterrupt): break
        return errors

    def close(self):
        failures = []
        for name in list(self.loaded):
            try:
                self.unload(name)
            except Exception:
                failures.append(name)
        if failures:
            raise ValueError('Could not finish extension cleanup: ' + ', '.join(failures))
