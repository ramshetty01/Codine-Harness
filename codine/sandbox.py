"""Restricted Docker execution for evaluation candidates; never falls back to host Python."""
import json
import os
import re
import selectors
import shutil
import subprocess
import time
import uuid

RUNNER = r'''
import contextlib, json, sys
payload = json.load(sys.stdin)
# Expected answers are never sent to this process.
try:
    namespace = {'__name__': 'candidate'}
    with contextlib.redirect_stdout(sys.stderr):
        exec(compile(payload['source'], 'task.py', 'exec'), namespace)
        actual = [namespace['solve'](*args) for args in payload['inputs']]
    print(json.dumps({'values': actual}, allow_nan=False))
except BaseException as error:
    print(json.dumps({'error': type(error).__name__ + ': ' + str(error)[:1000]}))
    sys.exit(1)
'''


class SandboxError(RuntimeError):
    pass


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
                '--log-driver', 'none', '--workdir', '/tmp',
                self.image, 'python', '-I', '-B', '-c', RUNNER]

    def evaluate(self, source, inputs):
        payload = json.dumps({'source': source, 'inputs': inputs}, allow_nan=False).encode()
        if len(payload) > 256000:
            raise SandboxError('Evaluation input exceeds 256 KB.')
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
                      'image': self.image, 'diagnostic': decoded['stderr'][-1500:]}
            if status == 'completed' and process.returncode == 0:
                try:
                    message = json.loads(decoded['stdout'])
                    if not isinstance(message, dict) or set(message) != {'values'} or not isinstance(message['values'], list):
                        raise ValueError('Invalid candidate result shape')
                    result['values'] = message['values']
                except ValueError:
                    result['status'] = 'invalid_output'
            elif status == 'completed':
                result['status'] = 'candidate_error'
                result['diagnostic'] += decoded['stdout'][-1500:]
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
