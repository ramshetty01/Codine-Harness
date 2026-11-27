"""Repeatable offline stress exercises; no credentials or model traffic.

python -m codine.stress --rounds 300 --files 5000 --commands 50
"""
import argparse
import gc
import json
import os
from pathlib import Path
import tempfile
import time
import tracemalloc
import uuid

from .agent import Agent
from .config import Config, Redactor
from .context import SUMMARY_KEYS
from .registry import ToolRegistry, ToolSpec
from .provenance import evaluation_manifest
from .sessions import Session, SessionStore, validate_messages
from .tools import run_command
from .workspace import Workspace


def descriptor_count():
    for path in ('/proc/self/fd', '/dev/fd'):
        if Path(path).is_dir():
            return len(os.listdir(path))
    return None


class OfflineProvider:
    def __init__(self):
        self.summaries = 0
        self.responses = 0

    def complete(self, messages, schemas, model, emit):
        validate_messages(messages)
        if not schemas:
            self.summaries += 1
            content = json.dumps({key: ['Offline fixture: preserve pending work; no model quality claim.'] for key in SUMMARY_KEYS})
        else:
            self.responses += 1
            content = 'Offline response. ' * 40
        return {'role': 'assistant', 'content': content}, 'stop', None


def run_stress(rounds=100, files=2000, commands=20):
    if not 10 <= rounds <= 2000 or not 10 <= files <= 10000 or not 1 <= commands <= 200:
        raise ValueError('Use 10–2000 rounds, 10–10000 files and 1–200 commands.')
    provenance = evaluation_manifest([{'id': 'offline-stress', 'rounds': rounds, 'files': files, 'commands': commands}], mode='offline-stress')
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix='codine-stress-') as temporary:
        root = Path(temporary).resolve()
        for index in range(files):
            directory = root / f'group{index // 100:03d}'
            directory.mkdir(exist_ok=True)
            (directory / f'file{index:05d}.py').write_text(f'number = {index}\n# stress needle\n')
        (root / '.env').write_text('NEVER_INCLUDE_STRESS_SECRET')
        workspace = Workspace(root, lambda _: True, lambda _: None)
        listing = workspace.list_files(limit=7)
        search = workspace.search('stress needle', limit=7)
        assert len(listing['files']) == 7 and listing['truncated']
        assert '.env' not in listing['files']
        assert search['truncated']
        target = root / 'group000/file00000.py'
        for _ in range(10):
            workspace._read(target)
        gc.collect()
        fds_before = descriptor_count()
        tracemalloc.start()
        memory_before = tracemalloc.get_traced_memory()[0]
        for _ in range(rounds * 10):
            workspace._read(target)
        gc.collect()
        memory_after, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        assert memory_after - memory_before < 1_000_000, 'Unexpected retained read-loop memory growth'
        for _ in range(commands):
            result = run_command(workspace, ['python', '-c', 'print("offline process")'], timeout=5)
            assert result['status'] == 'completed' and result['exit_code'] == 0
        fds_after = descriptor_count()
        if fds_before is not None and fds_after is not None:
            assert fds_after <= fds_before + 2, 'File descriptors accumulated across repeated reads/processes'
        store = SessionStore(root)
        try:
            session = Session.create(root, 'offline-stress', 'Keep the complete archive and pending task plan.')
            session.plan = [{'id': 'finish', 'title': 'Verify long-session behavior', 'status': 'pending'}]
            registry = ToolRegistry()
            registry.register(ToolSpec('noop', 'Offline declaration, never executed.', {'type': 'object'}, lambda: None))
            provider = OfflineProvider()
            agent = Agent(Config(root, context_chars=8000, context_tokens=20000, max_tokens=500), provider,
                          registry, store, session, lambda *args: None, Redactor())
            for index in range(rounds):
                assert agent.run_turn(f'Round {index}: keep the pending task and prior archive. ' + 'context ' * 30) == 'completed'
                if index % 10 == 9:
                    agent.session = store.load(session.id)
            saved = store.load(session.id)
            assert len(saved.messages) == 1 + rounds * 2
            assert saved.plan[0]['status'] == 'pending'
            assert provider.summaries > 0
            assert provider.responses == rounds
            validate_messages(saved.messages)
            # Repeated lock acquisition/release exercises descriptor lifecycle.
            for _ in range(rounds):
                another = SessionStore(root)
                another.close()
            summary_count = provider.summaries
        finally:
            store.close()
        fds_final = descriptor_count()
        if fds_before is not None and fds_final is not None:
            assert fds_final <= fds_before + 2
        return {'version': 1, 'provenance': provenance, 'mode': 'offline-stress', 'passed': True, 'inference_requests': 0,
                'rounds': rounds, 'repository_files': files, 'commands': commands,
                'descriptor_counts': {'before': fds_before, 'after_processes': fds_after, 'final': fds_final},
                'read_loop_retained_bytes': memory_after - memory_before, 'read_loop_peak_bytes': peak,
                'semantic_fixture_summaries': summary_count, 'archived_messages': 1 + rounds * 2,
                'seconds': round(time.monotonic() - started, 3),
                'limits': 'Synthetic offline stress; not live summary quality, OS-wide leak proof or an indefinite soak.'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rounds', type=int, default=100)
    parser.add_argument('--files', type=int, default=2000)
    parser.add_argument('--commands', type=int, default=20)
    parser.add_argument('--report', help='New report path; default is a unique preserved attempt')
    args = parser.parse_args(argv)
    path = Path(args.report) if args.report else Path('.codine/evals/stress-attempts') / (uuid.uuid4().hex + '.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    report = {'version': 1, 'mode': 'offline-stress', 'status': 'started', 'passed': False,
              'requested': {'rounds': args.rounds, 'files': args.files, 'commands': args.commands}, 'inference_requests': 0}
    try:
        with path.open('x') as stream:
            json.dump(report, stream, indent=2)
        os.chmod(path, 0o600)
    except OSError as error:
        parser.exit(1, f'Cannot create new stress report: {error}\n')
    failure = None
    try:
        report = run_stress(args.rounds, args.files, args.commands)
        report['status'] = 'completed'
    except (ValueError, AssertionError, OSError, RuntimeError) as error:
        failure = error
        report.update(status='failed', error_type=type(error).__name__, error=str(error))
    except KeyboardInterrupt:
        report.update(status='interrupted')
        raise
    finally:
        path.write_text(json.dumps(report, indent=2) + '\n')
    if failure:
        parser.exit(1, f'Offline stress failed: {failure}. Report: {path}\n')
    print(f"Offline stress passed: {args.rounds} turns, {args.files} files, {args.commands} processes; zero inference. Report: {path}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
