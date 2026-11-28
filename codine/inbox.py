"""Durable bounded input lanes; only Agent transfers items into conversation history."""
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import threading
import uuid

MAX_ITEMS = 32
MAX_TEXT = 16000
MAX_BYTES = 600000


def validate_receipts(receipts):
    if (not isinstance(receipts, list) or len(receipts) > 128 or
            any(not isinstance(item, str) or not re.fullmatch('[a-f0-9]{32}', item) for item in receipts) or
            len(set(receipts)) != len(receipts)):
        raise ValueError('Invalid queued-input delivery receipts.')


class Inbox:
    """One instance per locked session; RLock serializes UI and agent operations."""
    def __init__(self, store, session):
        store.acquire(session.id)
        self.path = store.path(session.id).with_suffix('.inbox')
        self.redactor = store.redactor
        self.lock = threading.RLock()
        self.cancel = threading.Event()
        self.failure = None
        self.items = self._load()
        self.reconcile(session.queue_receipts)
        self.deferred = {item['id'] for item in self.items}

    def _load(self):
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            return []
        with os.fdopen(fd, 'rb') as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_BYTES:
                raise ValueError('Unsafe or oversized input queue.')
            raw = handle.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise ValueError('Input queue grew beyond its byte limit.')
            data = json.loads(raw)
        if not isinstance(data, list) or len(data) > MAX_ITEMS:
            raise ValueError('Invalid input queue.')
        ids = set()
        for item in data:
            if (not isinstance(item, dict) or set(item) != {'id', 'lane', 'text'} or
                    not isinstance(item['id'], str) or not re.fullmatch('[a-f0-9]{32}', item['id']) or
                    item['id'] in ids or item['lane'] not in ('steer', 'followup') or
                    not isinstance(item['text'], str) or not item['text'].strip() or len(item['text']) > MAX_TEXT):
                raise ValueError('Invalid input queue item.')
            ids.add(item['id'])
        return data

    def _replace(self, items):
        encoded = json.dumps(items, ensure_ascii=False).encode('utf-8')
        if len(encoded) > MAX_BYTES:
            raise ValueError('Input queue exceeds its byte limit.')
        fd, path = tempfile.mkstemp(prefix='.inbox-', dir=self.path.parent)
        try:
            with os.fdopen(fd, 'wb') as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(path, self.path)
            descriptor = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            self.items = items
        except OSError as error:
            self.failure = error
            self.cancel.set()
            raise
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def reconcile(self, receipts):
        validate_receipts(receipts)
        with self.lock:
            remaining = [item for item in self.items if item['id'] not in receipts]
            if remaining != self.items:
                self._replace(remaining)

    def add(self, text, lane='followup'):
        text = self.redactor.text(text).strip()
        if lane not in ('steer', 'followup') or not text or len(text) > MAX_TEXT:
            raise ValueError(f'Input must contain 1–{MAX_TEXT} characters.')
        with self.lock:
            if len(self.items) >= MAX_ITEMS:
                raise ValueError(f'Queue is full ({MAX_ITEMS} items); use /drop ID.')
            item = {'id': uuid.uuid4().hex, 'lane': lane, 'text': text}
            self._replace(self.items + [item])
            return dict(item)

    def list(self, lane=None):
        with self.lock:
            return [dict(item) for item in self.items if lane is None or item['lane'] == lane]

    def ready(self, lane):
        with self.lock:
            return [item for item in self.list(lane) if item['id'] not in self.deferred]

    def defer_pending(self):
        with self.lock:
            self.deferred.update(item['id'] for item in self.items)

    def resume(self):
        with self.lock:
            self.deferred.clear()

    def remove(self, ident):
        with self.lock:
            matches = [item for item in self.items if item['id'].startswith(ident)]
            if not ident or len(matches) != 1:
                raise ValueError('Queue ID must identify exactly one pending item.')
            self._replace([item for item in self.items if item != matches[0]])

    def check_cancel(self):
        if self.failure:
            raise RuntimeError('Input queue save failed. Stop and inspect disk/session state before continuing.') from self.failure
        if self.cancel.is_set():
            raise KeyboardInterrupt()
