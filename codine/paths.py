"""Read-only installed code and user-owned writable state are separate."""
import os
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent
IS_CHECKOUT = (APP_ROOT / 'main.py').is_file() and (APP_ROOT / 'requirements.txt').is_file()
_home_override = os.getenv('CODINE_HOME')
CONFIG_ROOT = (Path(_home_override).expanduser().resolve() if _home_override else
               APP_ROOT if IS_CHECKOUT else
               Path(os.getenv('XDG_CONFIG_HOME', str(Path.home() / '.config'))) / 'codine')
STATE_ROOT = (Path(_home_override).expanduser().resolve() / 'state' if _home_override else
              APP_ROOT / '.codine' if IS_CHECKOUT else
              Path(os.getenv('XDG_STATE_HOME', str(Path.home() / '.local' / 'state'))) / 'codine')


def eval_resource(name):
    """Return a bundled fixture path; never accept arbitrary relative paths."""
    if name not in ('tasks.json', 'repository_tasks.json', 'command_tasks.json', 'dotenv_tasks.json', 'javascript_tasks.json'):
        raise ValueError('Unknown evaluation fixture.')
    directory = APP_ROOT / 'evals' if IS_CHECKOUT else Path(__file__).parent / 'resources' / 'evals'
    return directory / name
