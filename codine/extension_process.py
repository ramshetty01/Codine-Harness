"""Bounded extension child supervision, not an OS security sandbox."""
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import selectors
import shutil
import signal
import subprocess
import sys
import tempfile
import time

from .extension_manifest import LIMIT as SNAPSHOT_LIMIT, relative_path
from .extension_trust import binding
from .terminal import safe_text
from .transcripts import scrub

REGISTER_TIMEOUT = 5.0
INVOKE_TIMEOUT = 10.0
PROTOCOL_LIMIT = 65536
STDERR_LIMIT = 16384


class ExtensionProcessError(ValueError):
    pass


class ExtensionProcess:
    def __init__(self, snapshot, project, trust_store, redactor, cancel_event=None):
        self.snapshot = copy.deepcopy(snapshot)
        self.project = Path(project).resolve()
        self.trust_store, self.redactor, self.cancel_event = trust_store, redactor, cancel_event
        self.process = None
        self.stage = None
        self.closed = False
        self.sequence = 0
        self.descriptor = None
        self.stderr = ''
        try:
            self._trusted()
            self.stage = Path(tempfile.mkdtemp(prefix='codine-extension-'))
            self._stage()
            self._trusted()
            self._cancel()
            entry = self.snapshot['manifest']['entrypoint']
            caps = self.snapshot['manifest']['capabilities']
            self.process = subprocess.Popen(
                [sys.executable, '-I', '-u', str(Path(__file__).with_name('extension_worker.py').resolve()),
                 str(self.stage / entry), str(self.stage), json.dumps(caps)],
                cwd=self.project, env={'PATH': os.defpath, 'LANG': 'C.UTF-8'},
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                start_new_session=True, bufsize=0)
            for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
                os.set_blocking(stream.fileno(), False)
            self.descriptor = self._exchange({'operation': 'describe'}, REGISTER_TIMEOUT)
        except BaseException:
            self.close()
            raise

    def _trusted(self):
        binding(self.snapshot)
        info = self.project.stat()
        if (str(self.project) != self.snapshot['project'] or
                [info.st_dev, info.st_ino] != self.snapshot['project_identity'] or
                not self.trust_store.trusted(self.snapshot)):
            self.close()
            raise ExtensionProcessError('Extension trust is absent, revoked or belongs to another project.')

    def _stage(self):
        rows = self.snapshot['files']
        if not isinstance(rows, list) or not 1 <= len(rows) <= 32:
            raise ExtensionProcessError('Invalid extension snapshot file count.')
        seen, total = set(), 0
        for row in rows:
            name = relative_path(row['path'])
            if name in seen: raise ExtensionProcessError('Duplicate staged extension path.')
            seen.add(name)
            data = base64.b64decode(row['content_base64'], validate=True)
            total += len(data)
            if total > SNAPSHOT_LIMIT or hashlib.sha256(data).hexdigest() != row['sha256']:
                raise ExtensionProcessError('Extension snapshot bytes exceed bounds or mismatch hash.')
            path = self.stage / name
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'wb') as output: output.write(data)
        manifest = self.snapshot['manifest']
        if set(manifest['files']) != seen or manifest['entrypoint'] not in seen:
            raise ExtensionProcessError('Staged files do not match extension manifest.')

    def _cancel(self):
        if self.cancel_event is not None and self.cancel_event.is_set():
            self.close()
            raise KeyboardInterrupt('Extension operation cancelled.')

    def _clean(self, value):
        if isinstance(value, str): return safe_text(scrub(value, self.redactor))
        if isinstance(value, list): return [self._clean(item) for item in value]
        if isinstance(value, dict): return {self._clean(key): self._clean(item) for key, item in value.items()}
        return value

    def _exchange(self, request, timeout):
        self.sequence += 1
        raw = json.dumps({'version': 1, 'id': self.sequence, **request}, allow_nan=False).encode() + b'\n'
        if len(raw) > PROTOCOL_LIMIT: raise ExtensionProcessError('Extension request exceeds 64 KiB.')
        output, errors = bytearray(), bytearray()
        sent = 0
        deadline = time.monotonic() + timeout
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(self.process.stdin, selectors.EVENT_WRITE, 'stdin')
                selector.register(self.process.stdout, selectors.EVENT_READ, 'stdout')
                selector.register(self.process.stderr, selectors.EVENT_READ, 'stderr')
                while True:
                    self._cancel()
                    if time.monotonic() >= deadline: raise ExtensionProcessError('Extension operation timed out; not replayed.')
                    for key, _ in selector.select(min(.05, max(0, deadline-time.monotonic()))):
                        if key.data == 'stdin':
                            try: sent += os.write(key.fd, raw[sent:sent+4096])
                            except BlockingIOError: continue
                            if sent == len(raw): selector.unregister(key.fileobj)
                            continue
                        try: chunk = os.read(key.fd, 4096)
                        except BlockingIOError: continue
                        if not chunk:
                            selector.unregister(key.fileobj)
                            if key.data == 'stdout': raise ExtensionProcessError('Extension worker disconnected; not replayed.')
                            continue
                        target = output if key.data == 'stdout' else errors
                        target.extend(chunk)
                        if len(target) > (PROTOCOL_LIMIT if key.data == 'stdout' else STDERR_LIMIT):
                            raise ExtensionProcessError('Extension output limit exceeded; worker stopped.')
                    if b'\n' in output:
                        line, rest = output.split(b'\n', 1)
                        if rest: raise ExtensionProcessError('Unexpected extra extension protocol output.')
                        try:
                            response = json.loads(line, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                        except (ValueError, RecursionError):
                            raise ExtensionProcessError('Invalid extension response JSON.') from None
                        if (not isinstance(response, dict) or type(response.get('version')) is not int or response['version'] != 1 or
                                type(response.get('id')) is not int or response['id'] != self.sequence or type(response.get('ok')) is not bool or
                                set(response) != {'version', 'id', 'ok', 'result' if response['ok'] else 'error'}):
                            raise ExtensionProcessError('Invalid extension response envelope.')
                        self.stderr = self._clean(errors.decode('utf-8', errors='replace'))
                        if response['ok']: return self._clean(response['result'])
                        raise ExtensionProcessError('Extension handler failed: ' + self._clean(str(response['error'])))
        except BaseException:
            # A failed action is never retried. Retire the worker conservatively
            # even if it produced a well-formed handler error after side effects.
            self.close()
            raise

    def describe(self):
        self._cancel()
        if self.closed: raise ExtensionProcessError('Extension worker is closed.')
        self._trusted()
        return copy.deepcopy(self.descriptor)

    def invoke(self, kind, name, payload=None):
        self._cancel()
        if self.closed: raise ExtensionProcessError('Extension worker is closed.')
        self._trusted()
        return self._exchange({'operation': 'invoke', 'kind': kind, 'name': name, 'payload': payload}, INVOKE_TIMEOUT)

    def close(self):
        if self.closed: return
        self.closed = True
        if self.process is not None:
            try: os.killpg(self.process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                # macOS can report EPERM for an already-exited process group.
                # Reap the owned child and do not mask the original worker error.
                if self.process.poll() is None:
                    try: self.process.kill()
                    except ProcessLookupError: pass
            try: self.process.wait(timeout=2)
            except subprocess.TimeoutExpired: pass
            for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
                if stream is not None: stream.close()
        if self.stage is not None:
            shutil.rmtree(self.stage, ignore_errors=True)
