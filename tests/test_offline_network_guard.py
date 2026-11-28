import os
from pathlib import Path
import subprocess
import sys
import unittest


class OfflineNetworkGuardTests(unittest.TestCase):
    def test_only_registered_literal_loopback_is_allowed_and_scope_is_revoked(self):
        script = '''
import socket
from sitecustomize import allow_loopback

def denied(address):
    for call in (lambda: socket.create_connection(address, timeout=.1),
                 lambda: socket.socket().connect(address),
                 lambda: socket.socket().connect_ex(address)):
        try: call()
        except AssertionError: pass
        else: raise AssertionError('unexpected network permission')

with socket.socket() as listener:
    listener.bind(('127.0.0.1',0)); listener.listen()
    address=listener.getsockname()
    denied(address)
    with allow_loopback(address):
        for forbidden in [('example.com',443),('192.0.2.1',443),('localhost',address[1]),('127.0.0.1',1)]:
            denied(forbidden)
        with allow_loopback(address):
            with socket.create_connection(address,timeout=1): pass
            connection,_=listener.accept(); connection.close()
        with socket.create_connection(address,timeout=1): pass
        connection,_=listener.accept(); connection.close()
    denied(address)
    try:
        with allow_loopback(address): raise ValueError('fixture failed')
    except ValueError: pass
    denied(address)
'''
        root=Path(__file__).resolve().parents[1]
        env={key:value for key,value in os.environ.items() if key in ('PATH','SYSTEMROOT')}
        env['PYTHONPATH']=str(root/'scripts/offline_guard')
        result=subprocess.run([sys.executable,'-c',script],env=env,capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
