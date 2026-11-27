"""Tool declarations live next to bindings, with approved bounded execution."""
import os
import selectors
import shlex
import signal
import subprocess
import sys
import time
from .registry import ToolRegistry, ToolSpec


def stop_process(process):
    # Kill the whole process group, including children holding output pipes open.
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=0.3)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def approve_command(workspace, executor, argv, cwd, description):
    if workspace.mode == 'plan':
        raise ValueError('Commands are disabled in planning mode.')
    policy = getattr(workspace, 'command_policy', None)
    if policy is None:
        return workspace.approve(description)
    decision = policy.decide(executor=executor, project=workspace.root, cwd=cwd,
                             argv=argv, interactive=getattr(workspace, 'policy_interactive', False))
    workspace.last_policy_decision = decision
    workspace.emit('Policy: ' + decision.reason)
    if decision.action == 'deny':
        return False
    if decision.action == 'allow':
        # Still show the exact command/snapshot being authorized by this rule.
        workspace.emit(description)
        return True
    return workspace.approve(description + '\nPolicy: ' + decision.reason)


def command_rejection(workspace):
    decision = getattr(workspace, 'last_policy_decision', None)
    denied = decision is not None and decision.action == 'deny'
    return {'status': 'denied' if denied else 'declined', 'exit_code': None,
            'stdout': '', 'stderr': '', 'reason': decision.reason if decision else 'Approval declined.'}


def run_command(workspace, argv, cwd='.', timeout=30):
    if not argv or len(argv) > 100 or any(not isinstance(arg, str) or '\x00' in arg for arg in argv):
        raise ValueError('Provide 1–100 command arguments, with no null bytes.')
    directory = workspace.resolve(cwd, directory=True)
    if not directory.is_dir():
        raise ValueError('Command directory does not exist.')
    if not 1 <= timeout <= 120:
        raise ValueError('Command timeout must be 1–120 seconds.')
    policy_argv = tuple(argv)
    argv = list(argv)
    if argv[0] in ('python', 'python3'):
        argv[0] = sys.executable
    description = f'Run {shlex.join(argv)}\nWorking directory: {directory}\nTimeout: {timeout}s\nThis runs with your user permissions, outside a sandbox. Approve?'
    if not approve_command(workspace, 'host', policy_argv, directory.relative_to(workspace.root).as_posix(), description):
        return command_rejection(workspace)
    # Do not forward provider credentials. Programs can still read the filesystem.
    env = {key: os.environ[key] for key in ('PATH', 'HOME', 'LANG', 'LC_ALL', 'TMPDIR', 'SYSTEMROOT') if key in os.environ}
    env['PYTHONUNBUFFERED'] = '1'
    started = time.monotonic()
    process = subprocess.Popen(argv, cwd=directory, env=env, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    output = {'stdout': bytearray(), 'stderr': bytearray()}
    total, status = 0, 'completed'
    try:
        with selectors.DefaultSelector() as selector:
            for label, pipe in (('stdout', process.stdout), ('stderr', process.stderr)):
                os.set_blocking(pipe.fileno(), False)
                selector.register(pipe, selectors.EVENT_READ, label)
            while selector.get_map() or process.poll() is None:
                if time.monotonic() - started > timeout:
                    status = 'timeout'
                    stop_process(process)
                    break
                for key, _ in selector.select(timeout=0.05):
                    data = os.read(key.fileobj.fileno(), 8192)
                    if not data:
                        selector.unregister(key.fileobj)
                        continue
                    total += len(data)
                    remaining = 12000 - len(output[key.data])
                    if remaining > 0:
                        output[key.data].extend(data[:remaining])
                if total > 1_000_000:
                    status = 'output_limit'
                    stop_process(process)
                    break
            process.wait()
    except BaseException:
        stop_process(process)
        raise
    finally:
        process.stdout.close()
        process.stderr.close()
    return {'status': status, 'exit_code': process.returncode,
            **{name: data.decode('utf-8', errors='replace') for name, data in output.items()},
            'output_truncated': total > sum(map(len, output.values())),
            'seconds': round(time.monotonic() - started, 2)}


def execution_status(workspace):
    executor = getattr(workspace, 'command_executor', None)
    if executor is None:
        return 'HOST: commands run with your user filesystem/network permissions, outside a sandbox.'
    return ('DOCKER: command execution only; no host mounts or network; non-root/read-only root. '
            'File read/edit tools still operate in the host workspace. Image: ' + executor.image)


def run_docker_command(workspace, argv, files, cwd='.', timeout=30):
    from .staging import snapshot_repository
    from .sandbox import SandboxError
    if not 1 <= timeout <= 120:
        raise ValueError('Command timeout must be 1–120 seconds.')
    executor = workspace.command_executor
    argv = tuple(argv)
    manifest = snapshot_repository(workspace.root, files)
    # Validate before showing approval and retain these exact immutable bytes.
    try:
        executor.prepare_request(manifest, argv, cwd=cwd)
    except SandboxError as error:
        raise ValueError(str(error)) from error
    listing = '\n'.join(f'{entry.path}  {entry.size_bytes} bytes  sha256:{entry.sha256}' for entry in manifest.files)
    description = (f'Docker command: {shlex.join(argv)}\nCwd: /tmp/project/{cwd}\n'
                   f'Image: {executor.image}\nTimeout: {timeout}s\n'
                   f'Snapshot SHA256: {manifest.digest}\nSelected files:\n{listing}\n'
                   'No host mounts, no network; only this snapshot enters the container. Approve?')
    if not approve_command(workspace, 'docker', argv, cwd, description):
        return command_rejection(workspace)
    executor.timeout = timeout
    try:
        return executor.execute(manifest, argv, cwd=cwd)
    except SandboxError as error:
        raise ValueError(str(error)) from error


def build_registry(workspace, request_restart):
    registry = ToolRegistry(lambda spec: workspace.mode != "plan" or spec.effect == "read")
    def add(name, description, handler, properties, required=(), effect="read"):
        registry.register(ToolSpec(name, description, {'type': 'object', 'properties': properties,
                                'required': list(required), 'additionalProperties': False}, handler, effect))
    string = {'type': 'string'}
    def integer(minimum, maximum):
        return {'type': 'integer', 'minimum': minimum, 'maximum': maximum}
    add('list_files', 'List project files recursively; excludes credentials and generated directories. Narrow path if truncated.', workspace.list_files,
        {'path': string, 'recursive': {'type': 'boolean'}, 'limit': integer(1, 2000)})
    add('read_file', 'Read a page of UTF-8 text with line numbers and SHA256. Follow next_page to read more, passing expected_sha256 from the previous page. Read the exact region before editing.', workspace.read_file,
        {'path': string, 'start_line': integer(1, 10000000), 'max_lines': integer(1, 2000), 'start_char': integer(0, 2000000), 'expected_sha256': string}, ('path',))
    add('search', 'Search literal text in a project file or directory; returns bounded matching lines. Read before editing.', workspace.search,
        {'query': string, 'path': string, 'limit': integer(1, 200)}, ('query',))
    add('write_file', 'Create a NEW project text file, with diff and approval. Cannot overwrite existing files.', workspace.write_file,
        {'path': string, 'content': string}, ('path', 'content'), effect='write')
    add('edit_file', 'Replace exactly one occurrence of old_text previously read in the CURRENT file version. Shows diff, requires approval by default, rejects stale edits.', workspace.edit_file,
        {'path': string, 'old_text': string, 'new_text': string}, ('path', 'old_text', 'new_text'), effect='write')
    add('replace_lines', 'Replace an inclusive line range already read in the current file. Requires the SHA256 returned by read_file. Use this when exact substring quoting is difficult. Pass literal replacement text, including trailing newline when more lines follow. Shows diff and requires approval by default.', workspace.replace_lines,
        {'path': string, 'start_line': integer(1, 10000000), 'end_line': integer(1, 10000000), 'new_text': string, 'expected_sha256': string},
        ('path', 'start_line', 'end_line', 'new_text', 'expected_sha256'), effect='write')
    add('check_python', 'Validate Python syntax without executing the file.', workspace.check_python, {'path': string}, ('path',))
    if getattr(workspace, 'command_executor', None) is not None:
        add('run_command', 'Run argv in an isolated Docker container after approval. Explicit files are REQUIRED: only the staged UTF-8 snapshot enters the container. cwd is relative to that snapshot. No host mounts/network or installed host dependencies. File tools themselves still use the host workspace.',
            lambda **args: run_docker_command(workspace, **args),
            {'argv': {'type': 'array', 'items': string}, 'files': {'type': 'array', 'items': string},
             'cwd': string, 'timeout': integer(1, 120)}, ('argv', 'files'), effect='execute')
    else:
        add('run_command', 'Run an argv array without shell expansion, after explicit approval. Commands have full user filesystem/network privileges. Output bounded; default timeout 30 seconds. Use python for this harness interpreter.',
            lambda **args: run_command(workspace, **args), {'argv': {'type': 'array', 'items': string}, 'cwd': string, 'timeout': integer(1, 120)}, ('argv',), effect='execute')
    add('request_restart', 'Ask permission to save and restart Codine at a completed tool boundary, validating its code first. Reloads edited harness code and tools; keeps session. Do not call for ordinary project edits.', request_restart, {}, effect="restart")
    return registry
