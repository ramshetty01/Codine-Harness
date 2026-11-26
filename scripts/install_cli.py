"""Install this checkout's launcher in ~/.local/bin without changing directories."""
from pathlib import Path
import shlex


def main():
    root = Path(__file__).resolve().parents[1]
    python = root / '.venv' / 'bin' / 'python'
    if not python.is_file():
        raise SystemExit('Create .venv and install requirements.txt before installing the command.')
    destination = Path.home() / '.local' / 'bin' / 'codine'
    marker = '# Managed by Codine-Harness scripts/install_cli.py'
    if destination.exists() or destination.is_symlink():
        if destination.is_symlink() or marker not in destination.read_text():
            raise SystemExit(f'Refusing to replace an unrelated command: {destination}')
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        '#!/bin/sh\n' + marker + '\n'
        + 'exec ' + shlex.quote(str(python)) + ' '
        + shlex.quote(str(root / 'main.py')) + ' "$@"\n'
    )
    destination.chmod(0o755)
    print(f'Installed {destination}')
    print('Run codine from any project directory. Re-run this installer if the checkout moves.')


if __name__ == '__main__':
    main()
