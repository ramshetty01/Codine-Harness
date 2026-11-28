"""Local before-images for file-tool edits. Shell side effects are not tracked."""
import json
import base64
import hashlib
import os
from pathlib import Path
import re
import tempfile
import time
import uuid


class ChangeStore:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.directory = self.root / '.codine' / 'changes'
        if (self.root / '.codine').is_symlink() or self.directory.is_symlink():
            raise ValueError('Change history cannot be a symlink.')

    def _path(self, ident):
        if not re.fullmatch(r'[0-9]{19,20}-[0-9a-f]{8}', ident):
            raise ValueError('Invalid change ID.')
        path = self.directory / (ident + '.json')
        if path.is_symlink():
            raise ValueError('Change record cannot be a symlink.')
        return path

    def save(self, record):
        from .storage_lock import mutation
        with mutation(self.root):
            return self._save_storage(record)

    def _save_storage(self, record):
        # Revalidate directories in case another process replaced them.
        self.__init__(self.root)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.directory, 0o700)
        target = self._path(record['id'])
        fd, temporary = tempfile.mkstemp(dir=self.directory, prefix='.change-')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as handle:
                json.dump(record, handle, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
            descriptor = os.open(self.directory, os.O_RDONLY)
            try: os.fsync(descriptor)
            finally: os.close(descriptor)
        finally:
            if os.path.exists(temporary): os.unlink(temporary)

    def prepare(self, path, before, after_hash, session_id=None):
        record = {'id': str(time.time_ns()) + '-' + uuid.uuid4().hex[:8],
                  'path': path, 'before': before.decode('utf-8') if before is not None else None,
                  'after_hash': after_hash, 'undone': False, 'session_id': session_id}
        self.save(record)
        return record

    def prepare_lifecycle(self, operation, path, before, mode, destination=None, session_id=None):
        record = {'id': str(time.time_ns()) + '-' + uuid.uuid4().hex[:8],
                  'record_version': 2, 'kind': 'lifecycle', 'operation': operation,
                  'path': path, 'destination': destination, 'before_b64': base64.b64encode(before).decode('ascii'),
                  'before_hash': hashlib.sha256(before).hexdigest(), 'after_hash': hashlib.sha256(before).hexdigest(),
                  'mode': mode, 'phase': 'prepared', 'undone': False, 'session_id': session_id}
        self.save(record)
        return record

    def load(self, ident):
        path = self._path(ident)
        if path.stat().st_size > 12_000_000:
            raise ValueError('Change record is too large.')
        record = json.loads(path.read_text())
        if not isinstance(record, dict):
            raise ValueError('Invalid change record.')
        if record.get('kind') == 'lifecycle':
            from .file_lifecycle import load_bytes
            if (record.get('record_version') != 2 or record.get('id') != ident or
                    record.get('operation') not in ('move', 'delete') or not isinstance(record.get('path'), str) or
                    record.get('phase') not in ('prepared', 'copied', 'completed') or type(record.get('undone')) is not bool or
                    type(record.get('mode')) is not int or not 0 <= record['mode'] <= 0o7777):
                raise ValueError('Invalid lifecycle checkpoint.')
            if (record['operation'] == 'move' and not isinstance(record.get('destination'), str) or
                    record['operation'] == 'delete' and record.get('destination') is not None):
                raise ValueError('Invalid lifecycle destination.')
            try:
                before = load_bytes(record)
            except (ValueError, KeyError, TypeError) as error:
                raise ValueError('Invalid lifecycle before-image.') from error
            if (len(before) > 2_000_000 or hashlib.sha256(before).hexdigest() != record.get('before_hash') or
                    record.get('after_hash') != record.get('before_hash')):
                raise ValueError('Invalid lifecycle checkpoint hash or size.')
            for key in ('destination_identity', 'restore_identity'):
                value = record.get(key)
                if value is not None and (not isinstance(value, list) or len(value) != 2 or any(type(n) is not int or n < 0 for n in value)):
                    raise ValueError('Invalid lifecycle identity.')
            stamp = record.get('destination_stamp')
            if stamp is not None and (not isinstance(stamp, list) or len(stamp) != 6 or any(type(n) is not int or n < 0 for n in stamp)):
                raise ValueError('Invalid lifecycle destination version.')
            return record
        if (record.get('id') != ident or not isinstance(record.get('path'), str) or
                record.get('before') is not None and not isinstance(record['before'], str) or
                not isinstance(record.get('after_hash'), str) or
                not re.fullmatch('[0-9a-f]{64}', record['after_hash'])):
            raise ValueError('Invalid change record.')
        before = record.get('before')
        if before is not None and ('\x00' in before or len(before.encode('utf-8')) > 2_000_000):
            raise ValueError('Invalid checkpoint contents.')
        return record

    def list(self, limit=10):
        if not self.directory.exists(): return []
        self.__init__(self.root)
        records = []
        for path in sorted(self.directory.glob('*.json'), reverse=True):
            record = self.load(path.stem)
            if not record.get('undone'):
                records.append(record)
            if len(records) == limit: break
        return records
