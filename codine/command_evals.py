"""Offline reference-repair validation of pinned repository-derived command tasks."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import tempfile
import uuid

from .paths import eval_resource
from .provenance import evaluation_manifest
from .repository_executor import RepositoryExecutor
from .staging import StagingLimits, _validate_path, snapshot_repository


PACK_FILES = {'boltons': 'command_tasks.json', 'dotenv': 'dotenv_tasks.json', 'javascript': 'javascript_tasks.json'}

def validate_pack(pack):
    if pack.get('version') != 1 or not isinstance(pack.get('tasks'), list) or not pack['tasks']:
        raise ValueError('Expected nonempty version 1 command task pack.')
    provenance = pack.get('provenance', {})
    if not re.fullmatch('[a-f0-9]{40}', provenance.get('commit', '')) or not provenance.get('license'):
        raise ValueError('A full upstream commit and license are required.')
    common = pack.get('common_files')
    if not isinstance(common, dict) or not isinstance(common.get(provenance.get('staged_license_path')), str):
        raise ValueError('Upstream license must be retained in staged files.')
    source = common.get(provenance.get('source_path'))
    if not isinstance(source, str) or hashlib.sha256(source.encode()).hexdigest() != provenance.get('source_sha256'):
        raise ValueError('Pinned source hash does not match bundled source.')
    if provenance.get('license_sha256') is not None and hashlib.sha256(common[provenance['staged_license_path']].encode()).hexdigest() != provenance['license_sha256']:
        raise ValueError('Pinned license hash does not match bundled license.')
    seen = set()
    for task in pack['tasks']:
        if not isinstance(task.get('id'), str) or not task['id'] or task['id'] in seen:
            raise ValueError('Task IDs must be unique nonempty strings.')
        seen.add(task['id'])
        for field in ('public_command', 'final_command'):
            command = task.get(field)
            if not isinstance(command, list) or not command or any(not isinstance(arg, str) or not arg or '\0' in arg for arg in command):
                raise ValueError('Public/final commands must be explicit nonempty argv.')
        if task['public_command'] == task['final_command']:
            raise ValueError('Final checks must differ from public checks.')
        for files in (common, task.get('initial_overrides'), task.get('reference_replacements')):
            if not isinstance(files, dict):
                raise ValueError('Fixture file sets must be dictionaries.')
            for name, content in files.items():
                _validate_path(name, StagingLimits())
                if not isinstance(content, str) or '\0' in content:
                    raise ValueError('Fixtures must contain text files.')
        if not task['reference_replacements']:
            raise ValueError('Each task needs an explicit reference repair.')
    return pack


def run_task(pack, task, executor):
    """Checks run in fresh containers; only explicit fixture files are staged."""
    validate_pack(pack)
    if task not in pack['tasks']:
        raise ValueError('Task must belong to the validated pack.')
    with tempfile.TemporaryDirectory(prefix='codine-command-eval-') as directory:
        root = Path(directory).resolve()
        files = {**pack['common_files'], **task['initial_overrides']}
        def write(updates):
            for name, content in updates.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding='utf-8')
        def checks():
            manifest = snapshot_repository(root, list(files))
            rows = {}
            for label in ('public', 'final'):
                rows[label] = executor.execute(manifest, task[label + '_command'],
                    env={'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONHASHSEED': '0'})
            return rows
        write(files)
        broken = checks()
        files.update(task['reference_replacements'])
        write(task['reference_replacements'])
        repaired = checks()
        success = lambda result: result['status'] == 'completed' and result['exit_code'] == 0
        rejected = not all(success(row) for row in broken.values())
        passed = rejected and all(success(row) for row in repaired.values())
        return {'task': task['id'], 'kind': task['kind'], 'language': task['language'],
                'passed': passed, 'broken_fixture_rejected': rejected,
                'broken': broken, 'reference': repaired}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--offline', action='store_true', required=True)
    parser.add_argument('--pack', choices=tuple(PACK_FILES), default='boltons', help='Pinned repository-derived task corpus')
    parser.add_argument('--image', default='python:3.12-slim', help='Must already exist locally; never pulled')
    parser.add_argument('--task', action='append')
    parser.add_argument('--report-dir', default='.codine/evals/command-attempts')
    args = parser.parse_args(argv)
    fixture = PACK_FILES[args.pack]
    pack = validate_pack(json.loads(eval_resource(fixture).read_text()))
    selected = [task for task in pack['tasks'] if not args.task or task['id'] in args.task]
    if not selected or (args.task and set(args.task) - {task['id'] for task in selected}):
        parser.error('Unknown or empty task selection.')
    executor = RepositoryExecutor(args.image)
    # Hash every source fixture, reference patch, public/final command and license.
    identities = [{'id': task['id'], 'task': task, 'common_files': pack['common_files'],
                   'upstream': pack['provenance']} for task in selected]
    report = {'version': 1, 'mode': 'offline-reference-repair', 'inference_requests': 0,
              'meaning': 'Fixture/executor verification, NOT model solve rate.',
              'pack': args.pack, 'upstream': pack['provenance'],
              'provenance': evaluation_manifest(identities, mode='offline-reference-repair', image=executor.image),
              'results': []}
    directory = Path(args.report_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (uuid.uuid4().hex + '.json')
    def save():
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(report, indent=2) + '\n')
        temporary.replace(path)
    save()
    for task in selected:
        try:
            result = run_task(pack, task, executor)
        except Exception as error:
            result = {'task': task['id'], 'passed': False, 'error': str(error)}
        report['results'].append(result)
        save()
        print(f"{task['id']}: {'PASS' if result['passed'] else 'FAIL'}", flush=True)
    print(f'Zero inference calls. Report: {path}')
    return 0 if all(row['passed'] for row in report['results']) else 1


if __name__ == '__main__':
    raise SystemExit(main())
