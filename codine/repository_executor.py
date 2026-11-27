"""Bounded, isolated repository commands; no host execution fallback."""
import hashlib
import copy
import unicodedata
from pathlib import PurePosixPath

from .sandbox import DockerSandbox, SandboxError
from .execution_profiles import credential_name
from .staging import Manifest, StagingLimits, _validate_path

# Command stdout/stderr remain separate Docker streams. exec replaces the trusted
# bootstrap, so its exit status is the command's actual status (not a JSON claim).
COMMAND_RUNNER = r'''
import json, os, pathlib, sys
payload = json.load(sys.stdin)
root = pathlib.Path('/tmp/project')
root.mkdir(mode=0o700)
for name, content in payload['files'].items():
    target = root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding='utf-8')
    target.chmod(0o600)
os.chdir(root / payload['cwd'])
env = {'PATH': '/usr/local/bin:/usr/bin:/bin', 'HOME': '/tmp',
       'LANG': 'C.UTF-8', 'TMPDIR': '/tmp', 'PYTHONUNBUFFERED': '1'}
env.update(payload['env'])
# stdin carried the manifest; commands must not inherit it or prompt interactively.
with open('/dev/null', 'rb') as source:
    os.dup2(source.fileno(), 0)
os.execvpe(payload['argv'][0], payload['argv'], env)
'''

# Explicit non-secret build/test settings only. No host environment forwarding.
ENV_KEYS = frozenset({'CI', 'PYTHONDONTWRITEBYTECODE', 'PYTHONHASHSEED',
                      'NO_COLOR', 'TERM', 'TZ'})


class RepositoryExecutor(DockerSandbox):
    """Execute explicit argv in a fresh container created from a text manifest.

    The caller is responsible for approval and selecting files. This class does
    not wire itself into the interactive agent's command tool.
    """
    def create_args(self, name):
        args = super().create_args(name)
        args[-1] = COMMAND_RUNNER
        args[args.index('--network') + 1] = getattr(self, '_command_network', 'none')
        return args

    @staticmethod
    def prepare_request(manifest, argv, *, cwd='.', env=None):
        if not isinstance(manifest, Manifest) or not manifest.files:
            raise SandboxError('A nonempty staged Manifest is required.')
        limits = StagingLimits()
        files = {}
        seen = set()
        # Revalidate even constructed dataclasses; do not trust a caller-created
        # Manifest to contain paths produced by snapshot_repository.
        if len(manifest.files) > limits.max_files:
            raise SandboxError('Manifest exceeds file limit.')
        for entry in manifest.files:
            _validate_path(entry.path, limits)
            key = unicodedata.normalize('NFC', entry.path).casefold()
            if key in seen or not isinstance(entry.content, str) or '\0' in entry.content:
                raise SandboxError('Invalid or duplicate manifest entry.')
            seen.add(key)
            data = entry.content.encode('utf-8')
            if (len(data) != entry.size_bytes or len(data) > limits.max_file_bytes or
                    hashlib.sha256(data).hexdigest() != entry.sha256):
                raise SandboxError('Manifest size or content hash does not match.')
            files[entry.path] = entry.content
        if manifest.total_bytes > limits.max_total_bytes:
            raise SandboxError('Manifest exceeds total byte limit.')
        if (not isinstance(argv, (list, tuple)) or not 1 <= len(argv) <= 128 or
                any(not isinstance(arg, str) or '\0' in arg for arg in argv) or
                not argv[0] or sum(len(arg.encode()) for arg in argv) > 32768):
            raise SandboxError('Command must be a bounded, nonempty argv sequence.')
        if not isinstance(cwd, str) or '\\' in cwd or '\0' in cwd:
            raise SandboxError('Invalid command cwd.')
        path = PurePosixPath(cwd)
        if path.is_absolute() or str(path) != cwd or '..' in path.parts:
            raise SandboxError('Command cwd must stay within the staged project.')
        directories = {str(parent) for name in files for parent in PurePosixPath(name).parents}
        if cwd not in directories:
            raise SandboxError('Command cwd must be a staged directory.')
        env = {} if env is None else env
        if (not isinstance(env, dict) or any(key not in ENV_KEYS for key in env) or
                any(not isinstance(value, str) or '\0' in value or len(value.encode()) > 1024
                    for value in env.values())):
            raise SandboxError('Only bounded, explicitly allowed build environment settings are accepted.')
        return {'files': files, 'argv': list(argv), 'cwd': cwd, 'env': dict(env)}

    def execute(self, manifest, argv, *, cwd='.', env=None, on_output=None, network='none', credentials=None, cancel_event=None):
        payload = self.prepare_request(manifest, argv, cwd=cwd, env=env)
        if network not in ('none', 'bridge'):
            raise SandboxError('Command network must be none or bridge.')
        credentials = {} if credentials is None else credentials
        if (not isinstance(credentials, dict) or len(credentials) > 8 or
                any(not credential_name(name) for name in credentials) or
                any(not isinstance(value, str) or not value or '\0' in value or len(value.encode()) > 16384
                    for value in credentials.values()) or
                sum(len(value.encode()) for value in credentials.values()) > 32768):
            raise SandboxError('Invalid explicit command credentials.')
        if credentials and any(value in argument for value in credentials.values() for argument in argv):
            raise SandboxError('Credentials must not appear in command arguments.')
        payload['env'].update(credentials)
        # Each request owns its transport state; concurrent jobs cannot share network grants.
        transport = copy.copy(self)
        transport._command_network = network
        try:
            try:
                result = transport._run_payload(payload, payload_limit=50_000_000,
                                           on_output=None if credentials else on_output, cancel_event=cancel_event)
            except Exception:
                if credentials:
                    raise SandboxError('Credential-scoped command failed; output and transport details withheld.') from None
                raise
            if credentials:
                # Withholding all output also covers encoded/transformed credentials.
                # Copy only trusted typed status metadata, never arbitrary transport fields.
                result = {
                    'status': result.get('status') if result.get('status') in
                        ('completed', 'timeout', 'output_limit', 'cancelled') else 'failed',
                    'exit_code': result.get('exit_code') if type(result.get('exit_code')) is int else None,
                    'seconds': result.get('seconds') if type(result.get('seconds')) in (int, float) else None,
                    'image': self.image, 'stdout': '', 'stderr': '', 'output_withheld': True,
                    'notice': 'Credential-bearing command output withheld; status only.',
                }
            result.update({'manifest_digest': manifest.digest, 'argv': list(argv), 'cwd': cwd})
            return result
        finally:
            transport._command_network = 'none'
            for name in credentials:
                payload['env'].pop(name, None)
