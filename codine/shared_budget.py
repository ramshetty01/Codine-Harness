"""A key-bound durable allowance with owner-scoped, idempotent request receipts."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import uuid

from .budget import Budget, BudgetError, money

MAX_RECORDS = 10000


def session_owner(session):
    project = hashlib.sha256(str(Path(session.project).resolve()).encode()).hexdigest()[:24]
    return 'session:' + project + ':' + session.id


class SharedBudget:
    def __init__(self, path, api_key, *, owner, request_cap='0.25'):
        if not api_key or not isinstance(owner, str) or not 1 <= len(owner) <= 160:
            raise BudgetError('Shared spending requires a key and bounded owner identity.')
        self.path = Path(path)
        self.owner, self.request_cap = owner, str(money(request_cap))
        if not 0 < money(request_cap) <= money('3.50'):
            raise BudgetError('Invalid shared request reservation.')
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock_path = self.path.with_suffix('.lock')
        self.lock = None
        try:
            descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
            self.lock = os.fdopen(descriptor, 'r+')
            self._check_file(os.fstat(descriptor), 4_000_000)
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            identity = hashlib.sha256(api_key.encode()).hexdigest()
            try:
                descriptor = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            except FileNotFoundError:
                state = Budget.create('3.50', self.request_cap).state
                state.update(key_identity=identity)
            else:
                with os.fdopen(descriptor, 'r') as source:
                    self._check_file(os.fstat(source.fileno()), 4_000_000)
                    state = json.load(source)
            if not isinstance(state, dict) or state.get('key_identity') != identity:
                raise BudgetError('Spending key changed. Existing allowance cannot be reset by selecting another key.')
            self.ledger = Budget(state)
            if money(state['limit']) > money('3.50'):
                raise BudgetError('Unexpected shared spending limit.')
            migrated = 'shared_version' not in state
            if migrated:
                # Preserve the old evaluation total and unknown reservation exactly.
                state.update(shared_version=1, ledger_id=uuid.uuid4().hex, baseline_spent=state['spent'], records={}, reservation=None)
                if state['pending'] is not None:
                    ident = uuid.uuid4().hex
                    record = {'owner': 'evaluation', 'cap': state['pending'], 'phase': 'dispatched', 'cost': None}
                    state['records'][ident] = record
                    state['reservation'] = ident
            self.validate()
            if migrated:
                self.save()
        except BaseException:
            if self.lock:
                self.lock.close()
            raise

    @staticmethod
    def _check_file(info, limit):
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid() or
                info.st_mode & 0o022 or info.st_size > limit):
            raise BudgetError('Shared ledger/lock must be private user-owned regular files, not links.')

    def validate(self):
        state = self.ledger.state
        if type(state.get('shared_version')) is not int or state['shared_version'] != 1:
            raise BudgetError('Unsupported shared ledger version.')
        if not isinstance(state.get('ledger_id'), str) or not re.fullmatch(r'[0-9a-f]{32}', state['ledger_id']):
            raise BudgetError('Invalid durable ledger identity.')
        records = state.get('records')
        if not isinstance(records, dict) or len(records) > MAX_RECORDS:
            raise BudgetError('Invalid or full shared receipt history; review the ledger, never reset it.')
        total = money(state.get('baseline_spent'))
        pending = []
        for ident, record in records.items():
            if (not isinstance(ident, str) or not re.fullmatch(r'[0-9a-f]{32}', ident) or
                    not isinstance(record, dict) or set(record) != {'owner', 'cap', 'phase', 'cost'} or
                    not isinstance(record['owner'], str) or not 1 <= len(record['owner']) <= 160 or
                    not 0 < money(record['cap']) <= money(state['limit'])):
                raise BudgetError('Malformed shared request receipt.')
            if record['phase'] == 'settled':
                total += money(record['cost'])
            elif record['phase'] in ('reserved', 'dispatched') and record['cost'] is None:
                pending.append(ident)
            else:
                raise BudgetError('Malformed shared request state.')
        if total != money(state['spent']):
            raise BudgetError('Shared spending does not match durable receipts.')
        ident = state.get('reservation')
        if state['pending'] is None:
            if ident is not None or pending:
                raise BudgetError('Unexpected shared pending receipt.')
        elif pending != [ident] or records[ident]['cap'] != state['pending']:
            raise BudgetError('Shared reservation does not match its request receipt.')

    def save(self):
        self.validate()
        fd, temporary = tempfile.mkstemp(dir=self.path.parent, prefix='.budget-')
        try:
            with os.fdopen(fd, 'w') as handle:
                json.dump(self.ledger.state, handle)
                handle.flush(); os.fsync(handle.fileno())
            if self.path.is_symlink():
                raise BudgetError('Shared ledger cannot be a symlink.')
            os.replace(temporary, self.path)
            descriptor = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try: os.fsync(descriptor)
            finally: os.close(descriptor)
        finally:
            if os.path.exists(temporary): os.unlink(temporary)

    def reserve(self, request_id=None):
        state = self.ledger.state
        if state['pending'] is not None:
            raise BudgetError('Shared request cost is unknown or in progress. Reconcile its exact owner/request before another inference.')
        if len(state['records']) >= MAX_RECORDS:
            raise BudgetError('Shared receipt history is full; review it without resetting spending.')
        ident = request_id or uuid.uuid4().hex
        if not re.fullmatch(r'[0-9a-f]{32}', ident) or ident in state['records']:
            raise BudgetError('Request ID is invalid or already used.')
        state['request_cap'] = self.request_cap
        self.ledger.reserve()
        state['records'][ident] = {'owner': self.owner, 'cap': self.request_cap, 'phase': 'reserved', 'cost': None}
        state['reservation'] = ident
        self.save()
        return ident

    def record(self, request_id):
        record = self.ledger.state['records'].get(request_id)
        if record is None or record['owner'] != self.owner:
            raise BudgetError('Request receipt does not belong to this owner/session.')
        return record

    def dispatched(self, request_id):
        record = self.record(request_id)
        if self.ledger.state['reservation'] != request_id or record['phase'] != 'reserved':
            raise BudgetError('Request is not reserved for dispatch.')
        record['phase'] = 'dispatched'
        self.save()

    def settle(self, request_id, cost):
        record = self.record(request_id)
        actual = str(money(cost))
        if record['phase'] == 'settled':
            if money(record['cost']) != money(actual):
                raise BudgetError('Request already settled with a different charge.')
            return self.ledger.state['blocked']
        if self.ledger.state['reservation'] != request_id:
            raise BudgetError('Request is not the outstanding shared reservation.')
        blocked = self.ledger.settle(actual)
        record.update(phase='settled', cost=actual)
        self.ledger.state['reservation'] = None
        self.save()
        return blocked

    def summary(self):
        state = self.ledger.state
        ident = state['reservation']
        owner = state['records'][ident]['owner'] if ident else 'none'
        return (f"Shared key allowance: ${state['spent']} / ${state['limit']}; "
                f"pending={state['pending']}; request={ident}; owner={owner}. " + state['blocked'])

    def close(self):
        self.lock.close()

    def __enter__(self): return self
    def __exit__(self, *args): self.close()
