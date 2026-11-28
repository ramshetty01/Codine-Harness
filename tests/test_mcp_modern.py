"""Current stdio metadata and conservative, discovery-only legacy compatibility."""
import json
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

from codine.mcp_transport import MCPError
from tests import test_mcp as fixtures
SERVER=fixtures.SERVER

MODERN = r'''
import json,sys,time
from pathlib import Path
for line in sys.stdin:
 msg=json.loads(line)
 with Path('wire.jsonl').open('a') as log:log.write(json.dumps(msg)+'\n')
 method=msg.get('method')
 if method=='notifications/cancelled':
  Path('cancelled.json').write_text(json.dumps(msg));continue
 assert method!='initialize' and 'id' in msg
 meta=msg['params']['_meta']
 assert meta['io.modelcontextprotocol/protocolVersion']=='2026-07-28'
 assert meta['io.modelcontextprotocol/clientCapabilities']=={}
 assert meta['io.modelcontextprotocol/clientInfo']['name']=='codine'
 if method=='server/discover':
  result={'resultType':'complete','supportedVersions':['2026-07-28'],'capabilities':{'tools':{}}}
 elif method=='tools/list':
  result={'resultType':'complete','tools':[{'name':'echo','description':'Modern echo','inputSchema':{'type':'object','properties':{'text':{'type':'string'}},'required':['text']}}]}
 elif method=='tools/call':
  text=msg['params']['arguments']['text']
  if text=='wait':
   cancellation=json.loads(sys.stdin.readline())
   assert cancellation['method']=='notifications/cancelled'
   assert cancellation['params']['requestId']==msg['id']
   Path('cancelled.json').write_text(json.dumps(cancellation))
   continue
  if text=='input':result={'resultType':'input_required','inputRequests':{'ask':{'method':'sampling/createMessage','params':{}}}}
  elif text=='unsupported':
   print(json.dumps({'jsonrpc':'2.0','id':msg['id'],'error':{'code':-32022,'message':'Unsupported','data':{'supported':['2025-11-25']}}}),flush=True);continue
  elif text=='server-request':
   print(json.dumps({'jsonrpc':'2.0','id':'unexpected','method':'sampling/createMessage','params':{}}),flush=True)
   continue
  else:result={'resultType':'complete','content':[{'type':'text','text':text}]}
 else:raise RuntimeError(method)
 print(json.dumps({'jsonrpc':'2.0','id':msg['id'],'result':result}),flush=True)
'''


class ModernMCPTests(unittest.TestCase):
    save_config=fixtures.MCPTests.save_config
    trust=fixtures.MCPTests.trust
    connect=fixtures.MCPTests.connect
    call=fixtures.MCPTests.call

    def setUp(self):
        fixtures.MCPTests.setUp(self)
        self.script.write_text(MODERN)

    def wire(self):
        path=self.project/'wire.jsonl'
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def test_modern_discovery_and_each_request_metadata_without_initialize(self):
        self.assertEqual(self.connect()['protocol'],'2026-07-28')
        self.assertEqual(self.call('hello')['untrusted_server_result']['content'][0]['text'],'hello')
        wire=self.wire()
        self.assertEqual([row['method'] for row in wire],['server/discover','tools/list','tools/call'])
        self.assertTrue(all(row['params']['_meta']['io.modelcontextprotocol/clientCapabilities']=={} for row in wire))
        self.assertEqual(self.approve.call_count,1)

    def test_auto_legacy_error_fallback_but_explicit_legacy_skips_probe(self):
        logged=SERVER.replace(" method=msg['method']", " method=msg['method']\n with open('legacy-methods.txt','a') as log:log.write(method+'\\n')")
        self.script.write_text(logged)
        self.assertEqual(self.connect()['protocol'],'2025-11-25')
        self.assertEqual((self.project/'legacy-methods.txt').read_text().splitlines()[:3],['server/discover','initialize','tools/list'])
        self.manager.disconnect('local')
        (self.project/'legacy-methods.txt').unlink()
        self.row['protocol']='2025-11-25';self.save_config()
        with self.assertRaisesRegex(MCPError,'trust'):self.manager.connect('local')
        self.assertEqual(self.connect()['protocol'],'2025-11-25')
        self.assertEqual((self.project/'legacy-methods.txt').read_text().splitlines()[:2],['initialize','tools/list'])

    def test_strict_modern_and_recognized_modern_errors_never_downgrade(self):
        self.row['protocol']='2026-07-28';self.save_config();self.script.write_text(SERVER)
        self.trust()
        with self.assertRaises(MCPError):self.manager.connect('local')
        self.row['protocol']='auto';self.save_config()
        for code in (-32020,-32021,-32022):
            with self.subTest(code=code):
                self.script.write_text(MODERN.replace("  result={'resultType':'complete','supportedVersions':['2026-07-28'],'capabilities':{'tools':{}}}",
                    "  print(json.dumps({'jsonrpc':'2.0','id':msg['id'],'error':{'code':"+str(code)+",'message':'Modern error','data':{'supported':['2025-11-25']}}}),flush=True);continue"))
                self.trust()
                with self.assertRaises(MCPError):self.manager.connect('local')
                self.assertNotIn('initialize',[row['method'] for row in self.wire()])
                self.assertEqual(self.registry.tools,{})

    def test_input_required_and_later_version_errors_do_not_replay_tools(self):
        for argument in ('input','unsupported','server-request'):
            with self.subTest(argument=argument):
                self.connect()
                result=self.call(argument)
                self.assertIn('error',result)
                self.assertTrue(self.manager.loaded['local']['connection'].closed)
                calls=[row for row in self.wire() if row['method']=='tools/call']
                self.assertEqual(sum(row['params']['arguments']['text']==argument for row in calls),1)
                self.assertNotIn('initialize',[row['method'] for row in self.wire()])
                self.manager.disconnect('local')

    def test_timeout_probe_fails_closed_and_explains_explicit_legacy(self):
        self.script.write_text('import sys,time\nwith open("launch-count","a") as log:log.write("1")\nfor line in sys.stdin:time.sleep(30)\n')
        self.trust()
        with patch('codine.mcp_transport.TIMEOUT',.1):
            with self.assertRaisesRegex(MCPError,'protocol=2025-11-25'):
                self.manager.connect('local')
        self.assertEqual(self.registry.tools,{})
        self.assertEqual((self.project/'launch-count').read_text(),'1')

    def test_cancel_and_timeout_notify_exact_request_then_cleanup(self):
        for operation in ('cancel','timeout'):
            with self.subTest(operation=operation):
                self.workspace.cancel_event=threading.Event()
                self.connect()
                connection=self.manager.loaded['local']['connection']
                if operation=='cancel':
                    timer=threading.Timer(.05,self.workspace.cancel_event.set);timer.start()
                    try:
                        with self.assertRaises(KeyboardInterrupt):self.call('wait')
                    finally:timer.join()
                else:
                    with patch('codine.mcp_transport.TIMEOUT',.1):self.assertIn('error',self.call('wait'))
                cancellation=json.loads((self.project/'cancelled.json').read_text())
                call=[row for row in self.wire() if row['method']=='tools/call'][-1]
                self.assertEqual(cancellation['params']['requestId'],call['id'])
                self.assertTrue(connection.closed)
                self.assertIsNotNone(connection.process.poll())
                self.manager.disconnect('local')

    def test_malformed_discovery_and_unsupported_result_types_fail_closed(self):
        for bad in ("['2099-01-01']", "'2026-07-28'", "[]"):
            self.script.write_text(MODERN.replace("['2026-07-28']",bad))
            self.trust()
            with self.assertRaises(MCPError):self.manager.connect('local')
            self.assertEqual(self.registry.tools,{})
        self.script.write_text(MODERN.replace("'resultType':'complete'","'resultType':'unknown'"))
        self.trust()
        with self.assertRaisesRegex(MCPError,'result type'):self.manager.connect('local')

    def test_schema_dialect_and_protocol_preference_fail_closed(self):
        self.row['protocol']='2099-01-01';self.save_config()
        with self.assertRaisesRegex(ValueError,'protocol'):self.manager.list()
        self.row['protocol']='auto';self.save_config()
        self.script.write_text(MODERN.replace("'inputSchema':{'type':'object'", "'inputSchema':{'$schema':'https://untrusted.example/schema','type':'object'"))
        self.trust()
        with self.assertRaisesRegex(ValueError,'schema dialect'):self.manager.connect('local')
        self.assertEqual(self.registry.tools,{})

    def test_absent_resulttype_is_complete_for_backward_compatibility(self):
        self.script.write_text(MODERN.replace("'resultType':'complete',",''))
        self.connect()
        self.assertEqual(self.call('compatible')['untrusted_server_result']['content'][0]['text'],'compatible')
        self.assertEqual(self.manager.list()[0]['protocol'],'2026-07-28')

    def test_duplicate_error_code_never_selects_legacy_or_restarts(self):
        self.script.write_text(r'''import sys,json
from pathlib import Path
with Path('launch-count').open('a') as log:log.write('1')
for line in sys.stdin:
 msg=json.loads(line)
 with Path('methods').open('a') as log:log.write(msg['method']+'\n')
 print('{"jsonrpc":"2.0","id":'+str(msg['id'])+',"error":{"code":-32022,"code":-32601,"message":"ambiguous"}}',flush=True)
''')
        self.trust()
        with self.assertRaisesRegex(MCPError,'ambiguous'):self.manager.connect('local')
        self.assertEqual((self.project/'launch-count').read_text(),'1')
        self.assertEqual((self.project/'methods').read_text().splitlines(),['server/discover'])
        self.assertEqual(self.registry.tools,{})

    def test_malformed_result_metadata_and_deep_payload_retire_connection(self):
        from codine.mcp_transport import decode_frame
        for metadata in ("[]", "'wrong'", "None"):
            self.script.write_text(MODERN.replace("'supportedVersions':['2026-07-28']", "'_meta':"+metadata+",'supportedVersions':['2026-07-28']"))
            self.trust()
            with self.assertRaisesRegex(MCPError,'metadata'):self.manager.connect('local')
            self.assertEqual(self.registry.tools,{})
        with self.assertRaisesRegex(MCPError,'bounds'):
            decode_frame(b'{"value":'+b'['*40+b'0'+b']'*40+b'}')
        with self.assertRaisesRegex(MCPError,'bounds'):
            decode_frame(json.dumps({'value':[0]*10001}).encode())
        self.script.write_text(MODERN.replace("'content':[{'type':'text','text':text}]", "'_meta':[], 'content':[{'type':'text','text':text}]"))
        self.connect()
        result=self.call('once')
        self.assertIn('error',result)
        self.assertTrue(self.manager.loaded['local']['connection'].closed)
        self.assertEqual(len([row for row in self.wire() if row['method']=='tools/call']),1)

    def test_idle_connect_restores_previous_turn_cancellation_scope(self):
        from codine.mcp_client import command
        from types import SimpleNamespace
        from unittest.mock import Mock
        stale=threading.Event();stale.set();self.workspace.cancel_event=stale
        self.trust()
        agent=SimpleNamespace(_in_turn=False,mcp_manager=self.manager,redactor=self.redactor)
        command('connect local',agent,self.workspace,SimpleNamespace(write=Mock()))
        self.assertIs(self.workspace.cancel_event,stale);self.assertTrue(stale.is_set())
        self.assertFalse(self.manager.loaded['local']['connection'].cancel_event.is_set())
        with self.assertRaises(KeyboardInterrupt):self.call('cancelled active turn')
