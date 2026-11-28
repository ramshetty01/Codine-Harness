import json
from pathlib import Path
import sys
import tempfile
import unittest

from codine.sdk import Client, CodineError


class SDKResponseValidationTests(unittest.TestCase):
    def test_malformed_duplicate_nonfinite_and_unknown_responses_fail_closed(self):
        ready={'version':1,'sequence':1,'event':'ready','request_id':None,'data':{'protocol':1}}
        prefix='import json,sys\nprint('+repr(json.dumps(ready))+',flush=True)\nq=json.loads(sys.stdin.readline())\n'
        outputs=[
            '''print('{"version":1,"version":1,"id":'+json.dumps(q['id'])+',"result":{}}',flush=True)''',
            '''print('{"version":1,"id":'+json.dumps(q['id'])+',"result":NaN}',flush=True)''',
            '''print(json.dumps({'version':1,'id':q['id'],'error':'not-an-error-object'}),flush=True)''',
            '''print(json.dumps({'version':1,'id':'unknown-id','result':{}}),flush=True)''',
            '''print(json.dumps({'version':1,'id':q['id'],'result':{},'error':{'message':'both'}}),flush=True)''',
        ]
        with tempfile.TemporaryDirectory() as directory:
            for output in outputs:
                with self.subTest(output=output):
                    client=Client(Path(directory),command=[sys.executable,'-u','-c',prefix+output+'\nsys.stdin.read()\n'])
                    try:
                        with self.assertRaises(CodineError):client.status()
                        self.assertTrue(client.failure)
                        self.assertTrue(client.process.stdin.closed)
                    finally:client.close()

    def test_event_backlog_failure_requests_shutdown_without_running_callbacks(self):
        ready={'version':1,'sequence':1,'event':'ready','request_id':None,'data':{'protocol':1}}
        script='import json,sys\nprint('+repr(json.dumps(ready))+',flush=True)\nq=json.loads(sys.stdin.readline())\n'
        script+="for i in range(600): print(json.dumps({'version':1,'sequence':i+2,'event':'notice','request_id':q['id'],'data':'bounded'}),flush=True)\nsys.stdin.read()\n"
        with tempfile.TemporaryDirectory() as directory:
            client=Client(Path(directory),command=[sys.executable,'-u','-c',script])
            try:
                with self.assertRaises(CodineError):client.status()  # status intentionally does not drain callbacks
                self.assertTrue(client.process.stdin.closed)
                self.assertLessEqual(client.notifications.qsize(),512)
            finally:client.close()
