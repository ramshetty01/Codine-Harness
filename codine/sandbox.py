"""Restricted Docker execution for evaluation candidates; never falls back to host Python."""
import json
import os
from pathlib import PurePosixPath
import re
import selectors
import shutil
import subprocess
import time
import uuid

RUNNER = r'''
import contextlib, json, sys, pathlib, runpy
payload = json.load(sys.stdin)
# Expected answers are never sent to this process.
try:
    namespace = {'__name__': 'candidate'}
    with contextlib.redirect_stdout(sys.stderr):
        if 'files' in payload:
            root = pathlib.Path('/tmp/project')
            root.mkdir()
            for name, source in payload['files'].items():
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(source, encoding='utf-8')
            sys.path.insert(0, str(root))
            namespace = runpy.run_path(str(root / payload['entrypoint']))
        else:
            exec(compile(payload['source'], 'task.py', 'exec'), namespace)
        actual = [namespace['solve'](*args) for args in payload['inputs']]
    print(json.dumps({'values': actual}, allow_nan=False))
except BaseException as error:
    print(json.dumps({'error': type(error).__name__ + ': ' + str(error)[:1000]}))
    sys.exit(1)
'''


class SandboxError(RuntimeError):
    pass


def validate_files(files, entrypoint):
    """Accept a small, explicit Python-only manifest; never traverse a repository."""
    if not isinstance(files, dict) or not 1 <= len(files) <= 32:
        raise SandboxError('Expected 1–32 explicitly selected Python files.')
    for name, source in files.items():
        if not isinstance(name, str) or not isinstance(source, str):
            raise SandboxError('File paths and contents must be strings.')
        path = PurePosixPath(name)
        if (path.is_absolute() or str(path) != name or
                any(not part.isidentifier() for part in path.parts[:-1]) or
                not path.name.endswith('.py') or not path.name[:-3].isidentifier() or
                any(part in {'__pycache__', 'venv', 'node_modules'} for part in path.parts)):
            raise SandboxError('Only normalized relative Python module paths are allowed.')
    if entrypoint not in files:
        raise SandboxError('Entrypoint must be in the manifest.')
    if sum(len(source.encode('utf-8')) for source in files.values()) > 200000:
        raise SandboxError('Staged Python sources exceed 200 KB.')


def snapshot_files(root, paths, entrypoint):
    """Snapshot only manifest paths, rejecting symlinks and special files."""
    paths = list(paths)
    validate_files(dict.fromkeys(paths, ''), entrypoint)
    from .staging import snapshot_repository, StagingLimits, StagingError
    try:
        files = snapshot_repository(root, paths, StagingLimits(
            max_files=32, max_file_bytes=200000, max_total_bytes=200000)).as_text_files()
    except StagingError as error:
        raise SandboxError(str(error)) from error
    validate_files(files, entrypoint)
    return files


class DockerSandbox:
    def __init__(self, image='python:3.12-slim', timeout=5, output_limit=65536):
        if not 0 < timeout <= 120 or not 1024 <= output_limit <= 1_000_000:
            raise ValueError('Invalid sandbox timeout or output limit.')
        self.docker = shutil.which('docker')
        if not self.docker:
            raise SandboxError('Docker is required. There is no host-execution fallback.')
        self.timeout, self.output_limit = timeout, output_limit
        self._control(['info', '--format', '{{.ServerVersion}}'])
        ident = self._control(['image', 'inspect', '--format', '{{.Id}}', image]).strip()
        if not re.fullmatch(r'sha256:[0-9a-f]{64}', ident):
            raise SandboxError('Expected one locally installed image with a content-addressed ID.')
        self.image = ident  # Resolve the mutable tag once for the entire evaluation run.

    def _control(self, args):
        try:
            result = subprocess.run([self.docker, *args], stdin=subprocess.DEVNULL,
                                    capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise SandboxError('Docker control operation failed or timed out.') from error
        if result.returncode:
            raise SandboxError('Docker unavailable or image missing: ' + result.stderr[-800:])
        return result.stdout

    def create_args(self, name):
        return ['create', '--name', name, '--label', 'codine.eval=true', '--pull', 'never',
                '--interactive', '--network', 'none', '--read-only', '--cap-drop', 'ALL',
                '--security-opt', 'no-new-privileges:true', '--pids-limit', '32',
                '--memory', '128m', '--memory-swap', '128m', '--cpus', '1',
                '--user', '65534:65534', '--tmpfs', '/tmp:rw,noexec,nosuid,size=16m',
                '--log-driver', 'none', '--workdir', '/tmp', '--entrypoint', '/usr/bin/env',
                self.image, '-i', 'PATH=/usr/local/bin:/usr/bin:/bin', 'python', '-I', '-B', '-c', RUNNER]

    def evaluate(self, source, inputs):
        return self._evaluate_payload({'source': source, 'inputs': inputs})

    def evaluate_files(self, files, entrypoint, inputs):
        validate_files(files, entrypoint)
        return self._evaluate_payload({'files': files, 'entrypoint': entrypoint, 'inputs': inputs})

    def _evaluate_payload(self, data):
        result = self._run_payload(data)
        stdout, stderr = result.pop('stdout'), result.pop('stderr')
        result['diagnostic'] = stderr[-1500:]
        if result['status'] == 'completed' and result['exit_code'] == 0:
            try:
                message = json.loads(stdout)
                if not isinstance(message, dict) or set(message) != {'values'} or not isinstance(message['values'], list):
                    raise ValueError('Invalid candidate result shape')
                result['values'] = message['values']
            except ValueError:
                result['status'] = 'invalid_output'
        elif result['status'] == 'completed':
            result['status'] = 'candidate_error'
            result['diagnostic'] += stdout[-1500:]
        return result

    def _run_payload(self, data, payload_limit=256000):
        payload = json.dumps(data, allow_nan=False).encode()
        if len(payload) > payload_limit:
            raise SandboxError('Sandbox input exceeds serialized payload limit.')
        name = 'codine-eval-' + uuid.uuid4().hex
        started = time.monotonic()
        process = None
        try:
            self._control(self.create_args(name))
            process = subprocess.Popen([self.docker, 'start', '--attach', '--interactive', name],
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            output = {'stdout': bytearray(), 'stderr': bytearray()}
            offset, total, status = 0, 0, 'completed'
            with selectors.DefaultSelector() as selector:
                for label, pipe in [('stdin', process.stdin), ('stdout', process.stdout), ('stderr', process.stderr)]:
                    os.set_blocking(pipe.fileno(), False)
                    selector.register(pipe, selectors.EVENT_WRITE if label == 'stdin' else selectors.EVENT_READ, label)
                while selector.get_map() or process.poll() is None:
                    if time.monotonic() - started > self.timeout:
                        status = 'timeout'
                        break
                    for key, _ in selector.select(0.05):
                        if key.data == 'stdin':
                            try:
                                offset += os.write(key.fileobj.fileno(), payload[offset:offset+8192])
                            except BrokenPipeError:
                                offset = len(payload)
                            if offset == len(payload):
                                selector.unregister(key.fileobj)
                                key.fileobj.close()
                        else:
                            data = os.read(key.fileobj.fileno(), 8192)
                            if not data:
                                selector.unregister(key.fileobj)
                                continue
                            total += len(data)
                            remaining = self.output_limit - sum(map(len, output.values()))
                            if remaining > 0:
                                output[key.data].extend(data[:remaining])
                    if total > self.output_limit:
                        status = 'output_limit'
                        break
            if status != 'completed':
                process.kill()
            process.wait(timeout=5)
            decoded = {key: value.decode('utf-8', errors='replace') for key, value in output.items()}
            result = {'status': status, 'exit_code': process.returncode, 'seconds': round(time.monotonic()-started, 3),
                      'image': self.image, **decoded}
            return result
        finally:
            if process:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5)
                for pipe in (process.stdin, process.stdout, process.stderr):
                    pipe.close()
            # Remove only this run's uniquely named container, including all descendants.
            try:
                self._control(['rm', '--force', name])
            except SandboxError as error:
                if 'No such container' not in str(error):
                    raise SandboxError(f'Could not confirm sandbox cleanup. Inspect only container {name}.') from error
