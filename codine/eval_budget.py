"""One durable spending allowance across evaluation tasks and processes."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile
from .budget import Budget, BudgetError, money


class EvaluationBudget:
    def __init__(self, path, api_key):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock_path = self.path.with_suffix('.lock')
        if self.path.is_symlink() or lock_path.is_symlink():
            raise BudgetError('Evaluation ledger cannot be a symlink.')
        self.lock = lock_path.open('a+')
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            identity = hashlib.sha256(api_key.encode()).hexdigest()
            if self.path.exists():
                if self.path.stat().st_size > 10000:
                    raise BudgetError('Oversized evaluation ledger.')
                state = json.loads(self.path.read_text())
                if state.get('key_identity') != identity:
                    raise BudgetError('Evaluation key changed. Review the existing ledger before proceeding.')
                self.ledger = Budget(state)
                if money(state['limit']) > money('3.50') or money(state['request_cap']) != money('0.05'):
                    raise BudgetError('Unexpected evaluation spending limits.')
            else:
                self.ledger = Budget.create('3.50', '0.05')
                self.ledger.state['key_identity'] = identity
                self.save()
        except BaseException:
            self.lock.close()
            raise

    def save(self):
        fd, temporary = tempfile.mkstemp(dir=self.path.parent, prefix='.budget-')
        try:
            with os.fdopen(fd, 'w') as handle:
                json.dump(self.ledger.state, handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            descriptor = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def reserve(self):
        self.ledger.reserve()
        self.save()

    def settle(self, cost):
        blocked = self.ledger.settle(cost)
        self.save()
        if blocked:
            raise BudgetError(blocked)

    def close(self):
        self.lock.close()
