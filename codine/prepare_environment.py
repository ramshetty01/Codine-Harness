"""Explicit, bounded dependency preparation; candidate execution stays offline."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import stat
import subprocess
import tarfile
import time
import uuid

from .paths import STATE_ROOT
from .staging import StagingError, _read_file

VERSION = 1
CLEAN_ENTRYPOINT = ["/usr/bin/env", "-i", "PATH=/usr/local/bin:/usr/bin:/bin", "HOME=/tmp"]
SECRET = re.compile(r'(api[_-]?key|password|secret|credential|access[_-]?token)', re.I)
ENV_SECRET = re.compile(r'(api[_-]?key|password|secret|credential|token|access[_-]?key|private[_-]?key|authorization)', re.I)
AUTH_URL = re.compile(r'https?://[^/\s]+@', re.I)
IMAGE = re.compile(r'(?:sha256:[0-9a-f]{64}|[a-zA-Z0-9./:_-]+@sha256:[0-9a-f]{64})\Z')


class PreparationError(RuntimeError):
    pass


def selected_files(project, paths):
    root = Path(project).resolve()
    if not root.is_dir() or not 1 <= len(paths) <= 32:
        raise PreparationError('Select 1–32 lock/config files from an existing project.')
    result = {}
    for name in paths:
        path = Path(name)
        allowed = (path.name in {'pyproject.toml', 'package.json', 'package-lock.json', 'yarn.lock', 'uv.lock', 'poetry.lock', 'Pipfile', 'Pipfile.lock'} or
                   (path.name.startswith('requirements') and path.suffix in {'.txt', '.lock'}))
        if not allowed or path.is_absolute() or str(path) != name or any(p in {'.', '..', '.git', '.env', '.codine', '.ssh', '.aws', '.azure', '.kube', '.gnupg'} for p in path.parts):
            raise PreparationError('Only normalized, explicitly selected dependency lock/config paths are allowed.')
        target = root
        for part in path.parts:
            target /= part
            if target.is_symlink():
                raise PreparationError('Dependency manifests cannot contain symlinks.')
        root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            data = _read_file(root_fd, path.parts, 262144)
        except (StagingError, OSError) as error:
            raise PreparationError('Dependency file rejected: ' + str(error)) from error
        finally:
            os.close(root_fd)
        try:
            text = data.decode('utf-8')
        except UnicodeError as error:
            raise PreparationError('Dependency manifests must be UTF-8 text.') from error
        if SECRET.search(text) or AUTH_URL.search(text):
            raise PreparationError('Manifest may contain credentials; private authenticated dependencies are unsupported.')
        result[name] = data
    if sum(map(len, result.values())) > 1048576:
        raise PreparationError('Dependency manifests exceed 1 MB.')
    return result


def archive(files):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w') as tar:
        for name, content in sorted(files.items()):
            item = tarfile.TarInfo('codine-setup/' + name)
            item.size, item.mode, item.mtime = len(content), 0o644, 0
            tar.addfile(item, io.BytesIO(content))
    return output.getvalue()


def bounded_run(argv, payload=b'', timeout=30, limit=65536):
    """Bound both output and elapsed time without buffering unbounded pipe data."""
    process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    output, offset, started = bytearray(), 0, time.monotonic()
    try:
        with selectors.DefaultSelector() as selector:
            for pipe, event in ((process.stdin, selectors.EVENT_WRITE), (process.stdout, selectors.EVENT_READ)):
                os.set_blocking(pipe.fileno(), False)
                selector.register(pipe, event)
            while selector.get_map():
                if time.monotonic() - started > timeout:
                    raise PreparationError('Docker setup timed out.')
                for key, _ in selector.select(0.05):
                    if key.fileobj is process.stdin:
                        try:
                            offset += os.write(process.stdin.fileno(), payload[offset:offset+8192])
                        except BrokenPipeError:
                            offset = len(payload)
                        if offset == len(payload):
                            selector.unregister(process.stdin)
                            process.stdin.close()
                    else:
                        chunk = os.read(process.stdout.fileno(), 8192)
                        if not chunk:
                            selector.unregister(process.stdout)
                        output.extend(chunk)
                        if len(output) > limit:
                            raise PreparationError('Docker setup output exceeded its limit.')
        remaining = max(0.01, timeout - (time.monotonic() - started))
        if process.wait(timeout=remaining):
            # Dependency logs can expose resolved URLs; deliberately do not echo them.
            raise PreparationError('Docker operation failed; setup output withheld because it may contain private dependency data.')
        return output.decode('utf-8', errors='replace').strip()
    except subprocess.TimeoutExpired as error:
        raise PreparationError('Docker setup timed out.') from error
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        if not process.stdin.closed:
            process.stdin.close()
        process.stdout.close()


def prepared_identity_matches(info, key):
    config = info.get('Config', {})
    return ((config.get('Labels') or {}).get('codine.environment.key') == key and
            config.get('Entrypoint') == CLEAN_ENTRYPOINT and config.get('Cmd') == ['python3'] and
            config.get('User') == '65534:65534' and
            not any(ENV_SECRET.search(value.split('=', 1)[0]) or AUTH_URL.search(value)
                    for value in config.get('Env', []) or []))


class EnvironmentPreparer:
    def __init__(self, cache=None, run=bounded_run, docker=None):
        self.cache = Path(cache) if cache is not None else STATE_ROOT / 'environments'
        self.run = run
        self.docker = docker or shutil.which('docker')
        if not self.docker:
            raise PreparationError('Docker is required; no host fallback.')

    def cache_directory(self, create=False):
        """Walk an absolute path without following symlinks, holding the final fd."""
        path = self.cache.absolute()
        if '..' in path.parts:
            raise PreparationError('Cache directory must not contain traversal.')
        directory = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
        try:
            for part in path.parts[1:]:
                if create:
                    try:
                        os.mkdir(part, 0o700, dir_fd=directory)
                    except FileExistsError:
                        pass
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                os.close(directory)
                directory = child
            result, directory = directory, None
            return result
        except FileNotFoundError:
            if not create:
                return None
            raise
        except OSError as error:
            raise PreparationError('Cache directory must contain real directories, not symlinks.') from error
        finally:
            if directory is not None:
                os.close(directory)

    def read_receipt(self, name):
        directory = self.cache_directory()
        if directory is None:
            return None
        try:
            try:
                # Shared staging reader also rejects receipt hardlinks and changes during read.
                return _read_file(directory, (name,), 65536).decode('utf-8')
            except FileNotFoundError:
                return None
            except (OSError, StagingError, UnicodeError) as error:
                raise PreparationError('Cache receipt must be a stable private regular file, not a link.') from error
        finally:
            os.close(directory)

    def write_receipt(self, name, text):
        directory = self.cache_directory(create=True)
        temporary = name + '.' + uuid.uuid4().hex + '.tmp'
        try:
            try:
                target = os.stat(name, dir_fd=directory, follow_symlinks=False)
                if not stat.S_ISREG(target.st_mode) or target.st_nlink != 1:
                    raise PreparationError('Refusing linked or non-regular cache receipt.')
            except FileNotFoundError:
                pass
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 0o600, dir_fd=directory)
            with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
                output.write(text)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
        finally:
            try:
                os.unlink(temporary, dir_fd=directory)
            except FileNotFoundError:
                pass
            os.close(directory)

    def control(self, args, **kwargs):
        return self.run([self.docker, *args], **kwargs)

    def inspect(self, reference):
        info = json.loads(self.control(['image', 'inspect', '--format', '{{json .}}', reference]))
        if not re.fullmatch(r'sha256:[0-9a-f]{64}', info.get('Id', '')):
            raise PreparationError('Docker did not return an immutable image ID.')
        return info

    def prepare(self, project, paths, command, base_image, allow_network=False, timeout=300):
        if not IMAGE.fullmatch(base_image):
            raise PreparationError('Use a local immutable image ID or repository@sha256 digest, not a mutable tag.')
        if (not isinstance(command, list) or not 1 <= len(command) <= 64 or
                any(not isinstance(arg, str) or not arg or '\x00' in arg for arg in command) or
                len(json.dumps(command)) > 16384):
            raise PreparationError('Setup command must be a nonempty JSON argv array (maximum 64 arguments/16 KB).')
        if SECRET.search(' '.join(command)) or AUTH_URL.search(' '.join(command)):
            raise PreparationError('Credentials in setup arguments are unsupported.')
        if not 1 <= timeout <= 900:
            raise PreparationError('Setup timeout must be 1–900 seconds.')
        files = selected_files(project, paths)
        base = self.inspect(base_image)
        for value in base.get('Config', {}).get('Env', []) or []:
            if ENV_SECRET.search(value.split('=', 1)[0]) or AUTH_URL.search(value):
                raise PreparationError('Base image has credential-like environment configuration.')
        manifest = {name: hashlib.sha256(data).hexdigest() for name, data in sorted(files.items())}
        identity = {'version': VERSION, 'base_image': base['Id'],
                    'platform': {key: base.get(key, '') for key in ('Os', 'Architecture', 'Variant')},
                    'files': manifest,
                    'command_sha256': hashlib.sha256(json.dumps(command, separators=(',', ':')).encode()).hexdigest(), 'network': 'bridge' if allow_network else 'none'}
        key = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        receipt_name = key + '.json'
        saved_text = self.read_receipt(receipt_name)
        if saved_text is not None:
            try:
                saved = json.loads(saved_text)
                if not isinstance(saved, dict):
                    raise PreparationError('Invalid cached receipt.')
                if not re.fullmatch(r'sha256:[0-9a-f]{64}', saved.get('image', '')):
                    raise PreparationError('Invalid cached image ID.')
                cached = self.inspect(saved['image'])
                if (saved.get('identity') == identity and saved.get('key') == key and
                        cached['Id'] == saved['image'] and
                        prepared_identity_matches(cached, key)):
                    return {**saved, 'cached': True}
            except (OSError, ValueError, KeyError, PreparationError):
                pass
        name = 'codine-setup-' + uuid.uuid4().hex
        created_image, successful = None, False
        try:
            self.control(['create', '--name', name, '--label', 'codine.setup=true', '--pull', 'never',
                          '--network', identity['network'], '--cap-drop', 'ALL',
                          '--security-opt', 'no-new-privileges:true', '--pids-limit', '128',
                          '--memory', '512m', '--memory-swap', '512m', '--cpus', '1',
                          '--log-driver', 'none', '--user', '0:0', '--workdir', '/codine-setup',
                          '--entrypoint', '/usr/bin/env', base['Id'], '-i',
                          'PATH=/usr/local/bin:/usr/bin:/bin', 'HOME=/tmp', *command])
            self.control(['cp', '-', name + ':/'], payload=archive(files))
            self.control(['start', '--attach', name], timeout=timeout)
            # Docker attach completion is not proof of the contained program's status.
            status = json.loads(self.control(['inspect', '--format', '{{json .State}}', name]))
            if status.get('Running') or status.get('ExitCode') != 0 or status.get('OOMKilled'):
                raise PreparationError('Setup did not exit successfully; no image committed.')
            image = self.control(['commit', '--change', 'ENTRYPOINT ' + json.dumps(CLEAN_ENTRYPOINT), '--change', 'CMD ["python3"]',
                                  '--change', 'WORKDIR /tmp', '--change', 'USER 65534:65534',
                                  '--change', 'LABEL codine.environment.key=' + key, name], timeout=60)
            if re.fullmatch(r'sha256:[0-9a-f]{64}', image):
                created_image = image
            info = self.inspect(image)
            if not prepared_identity_matches(info, key):
                raise PreparationError('Committed image identity label did not match.')
            saved = {'key': key, 'image': info['Id'], 'identity': identity}
            self.write_receipt(receipt_name, json.dumps(saved, indent=2) + '\n')
            successful = True
            return {**saved, 'cached': False}
        finally:
            # Explicit named cleanup also terminates a container after attached-client timeout.
            self.control(['rm', '--force', name])
            if created_image and not successful:
                self.control(['image', 'rm', created_image])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', default='.')
    parser.add_argument('--file', action='append', required=True, dest='files')
    parser.add_argument('--base-image', required=True)
    parser.add_argument('--command-json', required=True, help='Explicit argv array; executed in setup container only')
    parser.add_argument('--allow-network', action='store_true', help='Explicitly permit setup networking only')
    parser.add_argument('--timeout', type=int, default=300)
    args = parser.parse_args(argv)
    try:
        result = EnvironmentPreparer().prepare(args.project, args.files, json.loads(args.command_json),
                                              args.base_image, args.allow_network, args.timeout)
        print(json.dumps({'image': result['image'], 'cache_key': result['key'], 'cached': result['cached']}))
        return 0
    except (PreparationError, OSError, ValueError) as error:
        parser.exit(1, f'Preparation failed: {error}\n')


if __name__ == '__main__':
    raise SystemExit(main())
