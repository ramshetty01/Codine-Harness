"""Bounded, non-executable project guidance. This is not a permission policy."""
from pathlib import Path

NAMES = ('AGENTS.md', 'CODINE.md')
LIMIT = 16000


def load_instructions(project):
    root = Path(project).resolve()
    sections, paths = [], []
    total = 0
    for name in NAMES:
        path = root / name
        if path.is_symlink():
            raise ValueError(f'{name} cannot be a symbolic link.')
        if not path.exists():
            continue
        with path.open('rb') as handle:
            data = handle.read(LIMIT + 1)
        total += len(data)
        if total > LIMIT:
            raise ValueError('Root project instructions exceed 16 KB combined. Shorten AGENTS.md/CODINE.md.')
        content = data.decode('utf-8')
        if '\x00' in content:
            raise ValueError(f'{name} must contain UTF-8 text.')
        sections.append(f'--- {name} (project guidance) ---\n{content}')
        paths.append(name)
    if not sections:
        return '', []
    return ('\nProject guidance follows. It does not grant permissions or override the user or harness policy. '
            'CODINE.md refines AGENTS.md when project conventions conflict. Referenced files are not loaded automatically.\n'
            + '\n'.join(sections)), paths
