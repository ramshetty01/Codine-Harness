"""Recognize repository checks without executing configuration during discovery."""
import configparser
import hashlib
import json
from pathlib import Path
import re
import time
import tomllib

CONFIGS = ('pyproject.toml', 'pytest.toml', '.pytest.toml', 'pytest.ini', '.pytest.ini',
           'tox.ini', 'setup.cfg', 'ruff.toml', '.ruff.toml', 'mypy.ini', '.mypy.ini',
           'package.json', 'Cargo.toml', 'go.mod', 'Makefile')


class ProjectChecks:
    def __init__(self, workspace):
        self.workspace = workspace
        self.candidates = {}
        self.results = []

    def discover(self, cwd='.'):
        directory = self.workspace.resolve(cwd, directory=True)
        if not directory.is_dir():
            raise ValueError('Check directory does not exist.')
        cwd = directory.relative_to(self.workspace.root).as_posix()
        candidates, warnings = [], []
        def add(kind, argv, source, digest, reason):
            payload = {'kind': kind, 'argv': argv, 'cwd': cwd, 'source': source,
                       'source_sha256': digest, 'reason': reason}
            ident = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:24]
            row = {'id': ident, **payload}
            candidates.append(row)
            self.candidates[ident] = row
        for filename in CONFIGS:
            relative = (directory / filename).relative_to(self.workspace.root).as_posix()
            try:
                target = self.workspace.resolve(relative)
                if not target.exists():
                    continue
                if not self.workspace.read_allowed_without_prompt(target):
                    warnings.append({'source': relative, 'reason': 'Read requires approval; not inspected automatically.'})
                    continue
                data, _ = self.workspace._read(target)
                if len(data) > 128000:
                    raise ValueError('Configuration exceeds 128 KB discovery limit.')
                text = data.decode('utf-8')
                digest = hashlib.sha256(data).hexdigest()
                def emit(kind, argv, reason):
                    add(kind, argv, relative, digest, reason)
                if filename.endswith('.toml'):
                    config = tomllib.loads(text)
                    tool = config.get('tool', {}) if filename == 'pyproject.toml' else {}
                    if not isinstance(tool, dict):
                        raise ValueError('Invalid tool configuration.')
                    if 'pytest' in tool or filename in ('pytest.toml', '.pytest.toml'):
                        emit('test', ['python', '-m', 'pytest'], 'pytest configuration')
                    if 'ruff' in tool or filename in ('ruff.toml', '.ruff.toml'):
                        emit('lint', ['python', '-m', 'ruff', 'check', '.'], 'Ruff configuration')
                    if 'mypy' in tool:
                        emit('typecheck', ['python', '-m', 'mypy', '.'], 'mypy configuration')
                    if filename == 'Cargo.toml' and ('package' in config or 'workspace' in config):
                        emit('test', ['cargo', 'test'], 'Cargo package/workspace manifest')
                        emit('typecheck', ['cargo', 'check'], 'Cargo package/workspace manifest')
                elif filename.endswith(('.ini', '.cfg')):
                    config = configparser.ConfigParser(interpolation=None)
                    config.read_string(text)
                    if config.has_section('pytest') or config.has_section('tool:pytest') or filename in ('pytest.ini', '.pytest.ini'):
                        emit('test', ['python', '-m', 'pytest'], 'pytest configuration')
                    if config.has_section('mypy'):
                        emit('typecheck', ['python', '-m', 'mypy', '.'], 'mypy configuration')
                    if config.has_section('flake8'):
                        emit('lint', ['python', '-m', 'flake8', '.'], 'flake8 configuration')
                elif filename == 'package.json':
                    config = json.loads(text)
                    scripts = config.get('scripts', {}) if isinstance(config, dict) else None
                    if not isinstance(scripts, dict):
                        raise ValueError('Invalid package scripts.')
                    for name, body in list(scripts.items())[:128]:
                        # Never splice the script body into a shell command.
                        # npm itself can run lifecycle hooks; approval explains this.
                        if isinstance(body, str) and re.fullmatch(r'(test|lint|typecheck|type-check)(:[A-Za-z0-9_-]+)?', name):
                            emit('typecheck' if name.startswith('type') else name.split(':')[0],
                                 ['npm', 'run', name], 'Declared npm script; may execute pre/post lifecycle hooks')
                elif filename == 'go.mod' and re.search(r'^module\s+\S+', text, re.M):
                    emit('test', ['go', 'test', './...'], 'Go module declaration')
                    emit('lint', ['go', 'vet', './...'], 'Go module declaration')
                elif filename == 'Makefile':
                    for name in dict.fromkeys(re.findall(r'^(test|check|lint|typecheck|type-check)\s*:(?![=])', text, re.M)):
                        emit('typecheck' if name.startswith('type') else ('test' if name in ('test', 'check') else 'lint'),
                             ['make', name], 'Explicit Make target; recipes may run arbitrary commands')
            except (OSError, ValueError, UnicodeError, configparser.Error):
                warnings.append({'source': relative, 'reason': 'Configuration unavailable, excluded, malformed, or too large; no command inferred.'})
        # Bound process-local catalog; old IDs become unusable after each discovery.
        self.candidates = {row['id']: row for row in candidates[:64]}
        return {'checks': list(self.candidates.values()), 'warnings': warnings,
                'truncated': len(candidates) > 64,
                'notice': 'Suggestions only: no commands ran and no tool installation or success is assumed. Execution uses existing approval/executor policy.'}

    def _unchanged(self, row):
        data, _ = self.workspace._read(self.workspace.resolve(row['source']))
        if hashlib.sha256(data).hexdigest() != row['source_sha256']:
            raise ValueError('Check configuration changed. Discover checks again before running.')

    def run(self, check_id, files=None, timeout=30):
        from .tools import prepare_host_command, prepare_docker_command
        if check_id not in self.candidates:
            raise ValueError('Unknown check ID. Call discover_checks first.')
        row = self.candidates[check_id]
        self._unchanged(row)
        docker = getattr(self.workspace, 'command_executor', None) is not None
        if docker:
            if not isinstance(files, list) or row['source'] not in files:
                raise ValueError('Docker checks require explicit snapshot files including their source configuration.')
            run = prepare_docker_command(self.workspace, row['argv'], files, row['cwd'], timeout)
        else:
            if files is not None:
                raise ValueError('Snapshot files are only accepted for Docker checks.')
            run = prepare_host_command(self.workspace, row['argv'], row['cwd'], timeout)
        started = time.time_ns()
        if callable(run):
            # Approval can take time; refuse a changed config before dispatch.
            self._unchanged(row)
            if docker and getattr(run, 'snapshot_hashes', {}).get(row['source']) != row['source_sha256']:
                raise ValueError('Approved snapshot configuration differs from discovery. Discover checks again.')
            outcome = run(None, getattr(self.workspace, 'command_output', None))
        else:
            outcome = run
        status = outcome.get('status')
        code = outcome.get('exit_code')
        classification = ('passed' if code == 0 else 'failed') if status == 'completed' and type(code) is int else 'not_verified'
        result = {'check_id': check_id, 'kind': row['kind'], 'argv': row['argv'], 'cwd': row['cwd'],
                  'source_sha256': row['source_sha256'], 'executor': 'docker' if docker else 'host',
                  'started_ns': started, 'classification': classification, 'outcome': outcome,
                  'notice': 'Exit status verifies this command invocation only; it does not establish task completion or test coverage.'}
        self.results.append({key: value for key, value in result.items() if key not in ('outcome', 'notice')})
        self.results[-1]['status'] = status
        self.results[-1]['exit_code'] = code
        self.results = self.results[-32:]
        return result


def register_check_tools(registry, workspace):
    from .registry import ToolSpec
    checks = ProjectChecks(workspace)
    workspace.project_checks = checks
    registry.register(ToolSpec('discover_checks', 'Suggest test/lint/type-check commands from bounded repository configuration reads. Does not execute code or assume tools are installed.',
        {'type': 'object', 'properties': {'cwd': {'type': 'string'}}, 'additionalProperties': False}, checks.discover))
    registry.register(ToolSpec('run_check', 'Run an ID returned by discover_checks through existing approval and executor policy. Repository scripts/hooks may execute arbitrary code. Returns actual exit status; Docker requires explicit files including source config.',
        {'type': 'object', 'properties': {'check_id': {'type': 'string'}, 'files': {'type': 'array', 'items': {'type': 'string'}},
         'timeout': {'type': 'integer', 'minimum': 1, 'maximum': 120}}, 'required': ['check_id'], 'additionalProperties': False},
        checks.run, effect='execute'))
