import io
import json
import os
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from codine.config import Redactor
from codine.rpc_terminal import RpcTerminal


class RpcProtocolTests(unittest.TestCase):
    def setUp(self):
        read,write=os.pipe()
        self.input=os.fdopen(read,'rb',buffering=0)
        self.writer=os.fdopen(write,'wb',buffering=0)
        self.output=io.StringIO()
        self.ui=RpcTerminal(Redactor(['secret-test-value']),self.input,self.output)
        self.inbox=SimpleNamespace(cancel=threading.Event())
        self.ui.bind_inbox(self.inbox);self.ui.start()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.writer.close();self.ui.reader.join(timeout=1)
        self.ui.close();self.input.close()

    def send(self,ident,method,params=None):
        self.writer.write((json.dumps({'version':1,'id':ident,'method':method,'params':params or {}})+'\n').encode())

    def wait(self,predicate):
        deadline=time.monotonic()+2
        while time.monotonic()<deadline:
            if predicate():return
            time.sleep(.01)
        self.fail('RPC observation timed out: '+self.output.getvalue()[-500:])

    def messages(self):return [json.loads(line) for line in self.output.getvalue().splitlines()]

    def test_duplicate_ids_never_queue_action_twice(self):
        self.send('a','command',{'text':'/save'});self.send('a','command',{'text':'/new'})
        self.wait(lambda:any(row.get('error') for row in self.messages()))
        self.assertEqual(self.ui.requests.qsize(),1)
        self.assertEqual(self.ui.prompt(),'/save')
        self.ui._finish()
        self.wait(lambda:len(self.messages())==2)
        self.assertIn('Duplicate',self.messages()[0]['error']['message'])

    def test_input_bounds_duplicate_json_and_command_queue_limit(self):
        self.writer.write(b'{"version":1,"version":1,"id":"dup","method":"status","params":{}}\n')
        for i in range(17):self.send(str(i),'run',{'text':'task'})
        self.wait(lambda:len(self.messages())>=2)
        self.assertEqual(self.ui.requests.qsize(),16)
        errors=[row['error']['message'] for row in self.messages()]
        self.assertTrue(any('Duplicate JSON' in row for row in errors))
        self.assertTrue(any('queue is full' in row for row in errors))

    def test_approval_expiry_and_disconnect_deny_without_hanging(self):
        with patch('codine.rpc_terminal.APPROVAL_TIMEOUT',.02):
            self.assertFalse(self.ui.approve('secret-test-value operation'))
        self.wait(lambda:bool(self.messages()))
        token=self.messages()[0]['data']['token']
        self.assertNotIn('secret-test-value',self.output.getvalue())
        self.send('late','approval',{'token':token,'allow':True})
        self.wait(lambda:any('error' in row for row in self.messages()))
        results=[]
        thread=threading.Thread(target=lambda:results.append(self.ui.approve('pending')))
        thread.start()
        self.wait(lambda:self.ui._approval_pending)
        self.writer.close();thread.join(timeout=1)
        self.assertEqual(results,[False]);self.assertTrue(self.inbox.cancel.is_set())

    def test_oversized_frame_disconnects_and_output_is_explicitly_truncated(self):
        self.ui.emit('result','x'*100000)
        self.wait(lambda:bool(self.messages()))
        self.assertTrue(self.messages()[0]['data']['truncated'])
        self.assertLess(len(self.output.getvalue()),65536)
        self.writer.write(b'x'*65537+b'\n')
        self.wait(lambda:self.ui.stopped.is_set())
        self.assertTrue(self.inbox.cancel.is_set())
