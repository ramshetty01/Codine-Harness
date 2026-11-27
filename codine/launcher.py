"""Portable entry boundary: reject unsupported platforms before POSIX imports."""
import sys


def platform_problem(platform=None, version=None):
    platform = sys.platform if platform is None else platform
    version = sys.version_info[:2] if version is None else version
    if version < (3, 11):
        return 'Codine requires Python 3.11 or newer; use a supported Python environment.'
    if platform not in ('linux', 'darwin'):
        return ('Codine currently supports macOS and Linux only. Native Windows is unsupported '
                'because process groups and file locks use POSIX APIs. WSL2 is experimental; '
                'run Python inside its Linux environment, not Windows Python. See docs/PLATFORMS.md.')
    return None


def main(argv=None):
    problem = platform_problem()
    if problem:
        print(problem, file=sys.stderr)
        return 2
    from .cli import main as run
    return run(argv)
