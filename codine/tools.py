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


def run_command(workspace, argv, cwd='.', timeout=30):
    if not argv or len(argv) > 100 or any(not isinstance(arg, str) or '\x00' in arg for arg in argv):
        raise ValueError('Provide 1–100 command arguments, with no null bytes.')
    directory = workspace.resolve(cwd, directory=True)
    if not directory.is_dir():
        raise ValueError('Command directory does not exist.')
    if not 1 <= timeout <= 120:
        raise ValueError('Command timeout must be 1–120 seconds.')
    argv = list(argv)
    if argv[0] in ('python', 'python3'):
        argv[0] = sys.executable
    description = f'Run {shlex.join(argv)}\nWorking directory: {directory}\nTimeout: {timeout}s\nThis runs with your user permissions, outside a sandbox. Approve?'
    if not workspace.approve(description):
        return {'status': 'declined', 'exit_code': None, 'stdout': '', 'stderr': ''}
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
    add('search', 'Search literal text in project files; returns bounded matching lines. Read before editing.', workspace.search,
        {'query': string, 'path': string, 'limit': integer(1, 200)}, ('query',))
    add('write_file', 'Create a NEW project text file, with diff and approval. Cannot overwrite existing files.', workspace.write_file,
        {'path': string, 'content': string}, ('path', 'content'), effect='write')
    add('edit_file', 'Replace exactly one occurrence of old_text previously read in the CURRENT file version. Shows diff, requires approval by default, rejects stale edits.', workspace.edit_file,
        {'path': string, 'old_text': string, 'new_text': string}, ('path', 'old_text', 'new_text'), effect='write')
    add('check_python', 'Validate Python syntax without executing the file.', workspace.check_python, {'path': string}, ('path',))
    add('run_command', 'Run an argv array without shell expansion, after explicit approval. Commands have full user filesystem/network privileges. Output bounded; default timeout 30 seconds. Use python for this harness interpreter.',
        lambda **args: run_command(workspace, **args), {'argv': {'type': 'array', 'items': string}, 'cwd': string, 'timeout': integer(1, 120)}, ('argv',), effect='execute')
    add('request_restart', 'Ask permission to save and restart Codine at a completed tool boundary, validating its code first. Reloads edited harness code and tools; keeps session. Do not call for ordinary project edits.', request_restart, {}, effect="restart")
    return registry
