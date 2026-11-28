"""CI network guard; only explicitly registered test-server loopback ports work."""
from collections import Counter
from contextlib import contextmanager
import socket
import threading

_connect = socket.socket.connect
_connect_ex = socket.socket.connect_ex
_create_connection = socket.create_connection
_allowed = Counter()
_lock = threading.RLock()


def _target(address):
    if (not isinstance(address, tuple) or len(address) < 2 or
            address[0] not in ('127.0.0.1', '::1') or
            type(address[1]) is not int or not 1 <= address[1] <= 65535):
        raise AssertionError('Network access is forbidden during offline Codine checks')
    return address[:2]


def _check(address):
    key = _target(address)
    with _lock:
        if not _allowed[key]:
            raise AssertionError('Unregistered loopback port is forbidden during offline Codine checks')


@contextmanager
def allow_loopback(address):
    """Trusted test fixture scope; never called by production provider code."""
    key = _target(address)
    with _lock:
        _allowed[key] += 1
    try:
        yield
    finally:
        with _lock:
            _allowed[key] -= 1
            if not _allowed[key]:
                del _allowed[key]


def connect(self, address):
    _check(address)
    return _connect(self, address)


def connect_ex(self, address):
    _check(address)
    return _connect_ex(self, address)


def create_connection(address, *args, **kwargs):
    _check(address)
    return _create_connection(address, *args, **kwargs)


socket.socket.connect = connect
socket.socket.connect_ex = connect_ex
socket.create_connection = create_connection
