"""Offline installed-wheel smoke check: python scripts/check_install.py /venv/bin/python."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def main():
    if len(sys.argv) != 2:
        raise SystemExit('Usage: python scripts/check_install.py /installed/venv/bin/python')
    python = str(Path(sys.argv[1]).resolve()) if not Path(sys.argv[1]).is_symlink() else str(Path(sys.argv[1]).absolute())
    with tempfile.TemporaryDirectory(prefix='codine-install-check-') as temporary:
        root = Path(temporary)
        project = root / 'project'
        project.mkdir()
        guard = root / 'guard'
        guard.mkdir()
        (guard / 'sitecustomize.py').write_text(
            'import socket\n'
            'def denied(*args, **kwargs):\n    raise AssertionError("Network forbidden in install check")\n'
            'socket.socket.connect = denied\nsocket.create_connection = denied\n')
        env = {key: value for key, value in os.environ.items() if key in ('PATH', 'SYSTEMROOT', 'TMPDIR')}
        env.update(HOME=str(root / 'home'), PYTHONPATH=str(guard), PYTHONDONTWRITEBYTECODE='1', NO_COLOR='1')

        def run(args):
            result = subprocess.run(args, cwd=project, env=env, capture_output=True, text=True, timeout=30)
            if result.returncode:
                raise AssertionError(f'Command failed: {args}\n{result.stdout}\n{result.stderr}')
            return result.stdout

        executable = str(Path(python).parent / 'codine')
        print(run([executable, '--version']).strip())
        print(run([executable, '--check']).strip())
        print(run([python, '-m', 'codine', '--check']).strip())
        assert not (project / '.codine').exists(), '--check must not create session state'
        code = '''
import json
from pathlib import Path
from codine.paths import APP_ROOT, CONFIG_ROOT, STATE_ROOT, IS_CHECKOUT, eval_resource
from model_picker import free_tool_models
from importlib.metadata import version
assert not IS_CHECKOUT
assert not CONFIG_ROOT.is_relative_to(APP_ROOT)
assert not STATE_ROOT.is_relative_to(APP_ROOT)
assert version('codine-harness')
for name in ('tasks.json', 'repository_tasks.json'):
    assert json.loads(eval_resource(name).read_text())['tasks']
assert free_tool_models([]) == []
print(json.dumps({'config':str(CONFIG_ROOT),'state':str(STATE_ROOT)}))
'''
        run([python, '-c', code])
        output = run([executable, '--provider', 'nvidia', '--prompt', '/help'])
        assert 'Commands' in output or '/help' in output
        assert list((project / '.codine' / 'sessions').glob('*.json'))
        # A restart must probe the installed module, not nonexistent site-packages/main.py.
        run([python, '-c', '''
from types import SimpleNamespace
from pathlib import Path
from codine.restart import restart_process
calls=[]
agent=SimpleNamespace(save=lambda:None, emit=lambda *args:None,
    session=SimpleNamespace(project=str(Path.cwd()),id='install-check'),
    store=SimpleNamespace(close=lambda:None,acquire=lambda _:None))
restart_process(agent,execute=lambda program,argv:calls.append(argv))
assert calls[0][1:3] == ['-m','codine']
'''])
        # Trusted local fixture exercises the installed worker's isolated import path.
        run([python, '-c', """
import json
from pathlib import Path
from codine.config import Redactor
from codine.workspace import Workspace
from codine.registry import ToolRegistry
from codine.extension_trust import ExtensionTrustStore
from codine.extension_manager import ExtensionManager
root=Path.cwd().resolve()
(root/'extension.py').write_text('def register(api):\\n    api.command("echo", "Echo input", lambda text:text)\\n    api.panel("status", lambda:"ready")\\n')
(root/'manifest.json').write_text(json.dumps({'version':1,'name':'install',
    'entrypoint':'extension.py','files':['extension.py'],'capabilities':['commands','ui']}))
ws=Workspace(root,lambda _:True,lambda _:None)
trust=ExtensionTrustStore(root.parent/'trust')
snapshot=trust.review(ws,'manifest.json')
trust.allow(ws,snapshot['digest'],lambda _:True)
manager=ExtensionManager(ws,ToolRegistry(),trust,Redactor())
try:
    manager.load('manifest.json')
    assert manager.invoke_command('install:echo','installed') == 'installed'
    assert manager.panel('install:status') == 'ready'
    manager.reload('install')
finally:
    manager.close()
assert manager.list() == []
"""])
        print(run([python, '-m', 'codine.evals', '--offline']).strip())
        report = json.loads((project / '.codine/evals/offline.json').read_text())
        assert report['passed'] == report['total'] == 12
        assert report['provenance']['source']['files']
        print('Installed wheel: imports, resources, offline startup, session location, restart and local extension lifecycle passed; parent network blocked.')


if __name__ == '__main__':
    main()
