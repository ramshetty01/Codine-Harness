import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from codine.config import Redactor
from codine.mcp_client import MCPManager, command
from codine.mcp_settings import MCPSettings
from codine.mcp_transport import MCPError
from codine.registry import ToolRegistry
from codine.workspace import Workspace

SERVER=r'''
import json,os,sys,time
initialized=False
for line in sys.stdin:
 msg=json.loads(line)
 if msg.get('method')=='notifications/initialized':
  initialized=True
  continue
 if 'method' not in msg:
  assert msg['error']['code']==-32601
  continue
 method=msg['method']
 if method=='server/discover':
  print(json.dumps({'jsonrpc':'2.0','id':msg['id'],'error':{'code':-32601,'message':'Method not found'}}),flush=True)
  continue
 if method=='initialize':
  assert msg['params']['capabilities']=={}
  result={'protocolVersion':'2025-11-25','capabilities':{'tools':{}},'serverInfo':{'name':'fixture','version':'1'}}
 elif method=='tools/list':
  assert initialized
  result={'tools':[{'name':'echo','description':'Echo text.','inputSchema':{'type':'object','properties':{'text':{'type':'string'}},'required':['text']},'annotations':{'readOnlyHint':True}}]}
 elif method=='tools/call':
  text=msg['params']['arguments']['text']
  if text=='sleep': time.sleep(30)
  if text=='die': os._exit(5)
  if text=='flood': sys.stderr.write('x'*20000);sys.stderr.flush();time.sleep(2)
  if text=='sample':
   print(json.dumps({'jsonrpc':'2.0','id':'sample-request','method':'sampling/createMessage','params':{}}),flush=True)
   reply=json.loads(sys.stdin.readline()); assert reply['error']['code']==-32601
  if text=='change': print(json.dumps({'jsonrpc':'2.0','method':'notifications/tools/list_changed'}),flush=True)
  if text=='credentials': text=os.getenv('MCP_TEST_TOKEN','missing')+'|'+str('OPENROUTER_API_KEY' in os.environ)
  result={'content':[{'type':'text','text':text}],'isError':text=='tool-error'}
 else: raise RuntimeError(method)
 print(json.dumps({'jsonrpc':'2.0','id':msg['id'],'result':result}),flush=True)
'''


class MCPTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.home=Path(temp.name).resolve();self.project=self.home/'project';self.project.mkdir()
        self.script=self.project/'server.py';self.script.write_text(SERVER)
        self.settings=MCPSettings(self.home/'mcp');self.settings.root.mkdir(mode=0o700)
        self.row={'transport':'stdio','argv':[str(Path(sys.executable).resolve()),'-I','-u',str(self.script)],
                  'cwd':str(self.project),'files':[str(self.script)],'credential_env':['MCP_TEST_TOKEN']}
        self.config=self.settings.root/'servers.json';self.save_config()
        self.approve=Mock(return_value=True)
        self.workspace=Workspace(self.project,self.approve,lambda _:None)
        self.registry=ToolRegistry(allowed=lambda spec:self.workspace.mode!='plan' or spec.effect=='read')
        self.redactor=Redactor()
        self.manager=MCPManager(self.workspace,self.registry,self.redactor,self.settings,
                                {'MCP_TEST_TOKEN':'private-token-xyz','OPENROUTER_API_KEY':'never-forward-this'})
        self.addCleanup(self.manager.close)
        self.ui=SimpleNamespace(interactive=True,write=Mock(),approve=Mock(return_value=True))
        self.agent=SimpleNamespace(mcp_manager=self.manager,redactor=self.redactor,_in_turn=False)

    def save_config(self):
        self.config.write_text(json.dumps({'version':1,'servers':{'local':self.row}}));self.config.chmod(0o600)

    def trust(self):
        self.assertTrue(self.settings.approve('local',self.project,self.ui))

    def connect(self):
        self.trust();return self.manager.connect('local')

    def call(self,text):
        name=next(iter(self.registry.tools))
        return json.loads(self.registry.execute({'function':{'name':name,'arguments':json.dumps({'text':text})}}))

    def test_handshake_discovery_approval_credential_redaction_and_no_sampling(self):
        with self.assertRaisesRegex(MCPError,'trust'): self.manager.connect('local')
        row=self.connect();self.assertEqual(row['protocol'],'2025-11-25')
        self.assertTrue(row['tools'][0].startswith('mcp_local_'))
        self.assertEqual(self.call('hello')['untrusted_server_result']['content'][0]['text'],'hello')
        self.assertEqual(self.approve.call_count,1)
        output=self.call('credentials')['untrusted_server_result']['content'][0]['text']
        self.assertNotIn('private-token-xyz',output);self.assertIn('False',output)
        self.assertEqual(self.call('sample')['untrusted_server_result']['content'][0]['text'],'sample')
        self.assertTrue(self.call('tool-error')['untrusted_server_result']['isError'])
        self.assertFalse(self.manager.loaded['local']['connection'].closed)
        self.assertNotIn('private-token-xyz',(self.settings.root/'trust/receipts.json').read_text())

    def test_approval_decline_and_plan_block_even_readonly_hint(self):
        self.connect();self.approve.return_value=False
        self.assertEqual(self.call('hello'),{'status':'declined'})
        self.workspace.mode='plan'
        self.assertEqual(self.registry.schemas(),[])
        self.assertIn('error',self.call('hello'))
        with self.assertRaises(MCPError): self.manager.connect('local')
        command('disconnect local',self.agent,self.workspace,self.ui)
        self.assertEqual(self.registry.tools,{})

    def test_launch_changed_or_revoked_cannot_invoke(self):
        self.connect();self.script.write_text(SERVER+'\n# modified\n')
        self.assertIn('error',self.call('hello'))
        self.assertTrue(self.manager.loaded['local']['connection'].closed)
        self.manager.disconnect('local');self.trust();self.manager.connect('local')
        self.settings.revoke('local',self.project)
        self.assertIn('error',self.call('hello'))

    def test_change_during_trust_and_call_approval_rejected(self):
        self.ui.approve.side_effect=lambda _: self.script.write_text(SERVER+'\n# changed\n') or True
        with self.assertRaisesRegex(ValueError,'during approval'): self.trust()
        self.ui.approve.side_effect=None;self.connect()
        self.approve.side_effect=lambda _: self.settings.revoke('local',self.project) or True
        self.assertIn('error',self.call('hello'))

    def test_notifications_require_reconnect_and_no_core_replacement(self):
        self.connect();self.call('change')
        self.assertIn('error',self.call('again'))
        self.manager.disconnect('local');self.assertEqual(self.registry.tools,{})

    def test_timeout_disconnect_flood_and_cancel_stop_without_replay(self):
        for text in ('sleep','die','flood'):
            with self.subTest(text=text):
                self.connect()
                with patch('codine.mcp_transport.TIMEOUT',.15): self.assertIn('error',self.call(text))
                conn=self.manager.loaded['local']['connection'];self.assertTrue(conn.closed)
                self.assertIsNotNone(conn.process.poll());self.manager.disconnect('local')
        self.connect();event=threading.Event();self.workspace.cancel_event=event
        timer=threading.Timer(.1,event.set);timer.start()
        try:
            with self.assertRaises(KeyboardInterrupt): self.call('sleep')
        finally: timer.join()
        self.assertTrue(self.manager.loaded['local']['connection'].closed)

    def test_private_config_and_provider_environment_are_enforced(self):
        self.config.chmod(0o644)
        with self.assertRaises(ValueError): self.manager.list()
        self.config.chmod(0o600)
        self.row['credential_env']=['OPENROUTER_API_KEY'];self.save_config()
        with self.assertRaises(ValueError): self.manager.list()
        self.row['credential_env']=['NODE_OPTIONS'];self.save_config()
        with self.assertRaises(ValueError): self.manager.list()
        self.row['credential_env']=['MCP_TEST_TOKEN'];self.row['transport']='http';self.save_config()
        with self.assertRaisesRegex(ValueError,'stdio'):self.manager.list()

    def test_custom_config_protected_and_cli_lifecycle_explicit(self):
        broad=Workspace(self.home,lambda _:True,lambda _:None)
        manager=MCPManager(broad,ToolRegistry(),Redactor(),self.settings,{})
        self.addCleanup(manager.close)
        for operation in ('read','write'):
            with self.assertRaises(ValueError): broad.resolve('mcp/servers.json',operation=operation)
        command('trust local',self.agent,self.workspace,self.ui)
        event=threading.Event();event.set();self.workspace.cancel_event=event
        command('connect local',self.agent,self.workspace,self.ui)
        self.assertTrue(event.is_set())
        self.assertIs(self.workspace.cancel_event,event)
        command('revoke local',self.agent,self.workspace,self.ui)
        self.assertEqual(self.registry.tools,{})
        self.agent._in_turn=True
        with self.assertRaises(MCPError): command('connect local',self.agent,self.workspace,self.ui)

    def test_schema_and_version_failures_do_not_register_tools(self):
        for source in (SERVER.replace("'2025-11-25'","'2026-07-28'"),
                       SERVER.replace("'type':'string'","'oneOf':[]")):
            self.script.write_text(source);self.trust()
            with self.assertRaises(ValueError): self.manager.connect('local')
            self.assertEqual(self.registry.tools,{})

    def test_pagination_and_changed_metadata_are_bounded(self):
        page = SERVER.replace(" elif method=='tools/call':", "  if not msg.get('params',{}).get('cursor'): result={'tools':[],'nextCursor':'page2'}\n elif method=='tools/call':")
        self.script.write_text(page);self.connect()
        self.assertEqual(len(self.registry.tools),1)
        self.manager.disconnect('local')
        repeated = page.replace("if not msg.get('params',{}).get('cursor'):", 'if True:')
        self.script.write_text(repeated);self.trust()
        with self.assertRaisesRegex(MCPError,'cursor'): self.manager.connect('local')
        self.assertEqual(self.registry.tools,{})

    def test_stdout_bounds_and_invalid_json_retire_connection(self):
        for payload in ('print("not json",flush=True);time.sleep(20)',
                        'print("x"*70000,flush=True);time.sleep(20)'):
            with self.subTest(payload=payload):
                self.script.write_text(SERVER.replace("  if text=='sleep': time.sleep(30)",
                                                     "  if text=='bad': "+payload))
                self.connect()
                result=self.call('bad')
                self.assertIn('error',result)
                self.assertTrue(self.manager.loaded['local']['connection'].closed)
                self.manager.disconnect('local')

    def test_no_literal_secret_in_trust_review_or_receipt(self):
        self.row['argv'].append('private-token-xyz');self.save_config()
        with self.assertRaisesRegex(ValueError,'credential values'):
            command('trust local',self.agent,self.workspace,self.ui)
        self.assertFalse(self.ui.write.called)
        self.assertFalse((self.settings.root/'trust/receipts.json').exists())

    def test_changed_declared_script_never_executes_in_check_to_launch_gap(self):
        import subprocess
        original=subprocess.Popen
        marker=self.project/'UNAPPROVED_EXECUTED'
        self.trust()
        stages=[]
        def mutate_before_spawn(argv,**kwargs):
            stages.append(Path(argv[-1]))
            self.script.write_text('from pathlib import Path\nPath("UNAPPROVED_EXECUTED").touch()\n'+SERVER)
            return original(argv,**kwargs)
        with patch('codine.mcp_transport.subprocess.Popen',side_effect=mutate_before_spawn):
            with self.assertRaisesRegex(MCPError,'changed during discovery'):
                self.manager.connect('local')
        self.assertFalse(marker.exists())
        self.assertNotEqual(stages[0],self.script)
        self.assertFalse(stages[0].exists())
        self.assertEqual(self.registry.tools,{})

    def test_declared_sibling_import_uses_frozen_bytes_and_original_cwd(self):
        helper=self.project/'helper.py';helper.write_text('VALUE="FROZEN_HELPER"\n')
        self.script.write_text(SERVER.replace('import json,os,sys,time','import json,os,sys,time\nfrom helper import VALUE').replace("  text=msg['params']['arguments']['text']", "  text=VALUE+'|'+os.getcwd()"))
        self.row['argv'].remove('-I')
        self.row['files'].append(str(helper));self.save_config()
        self.connect()
        result=self.call('anything')['untrusted_server_result']['content'][0]['text']
        self.assertEqual(result,'FROZEN_HELPER|'+str(self.project))
        connection=self.manager.loaded['local']['connection']
        staged=Path(connection.staged.name)
        self.assertTrue(staged.exists())
        self.manager.disconnect('local')
        self.assertFalse(staged.exists())

    def test_declared_stage_size_failure_and_cancel_remove_temporary_files(self):
        self.trust()
        with patch('codine.mcp_transport.STAGE_LIMIT',100):
            with self.assertRaisesRegex(MCPError,'aggregate'):
                self.manager.connect('local')
        self.assertEqual(self.registry.tools,{})
        self.manager.connect('local')
        connection=self.manager.loaded['local']['connection']
        stage=Path(connection.staged.name)
        event=threading.Event();self.workspace.cancel_event=event
        timer=threading.Timer(.05,event.set);timer.start()
        try:
            with self.assertRaises(KeyboardInterrupt):self.call('sleep')
        finally:timer.join()
        self.assertFalse(stage.exists())
        self.assertIsNotNone(connection.process.poll())

    def test_server_request_batched_with_result_receives_refusal_before_next_call(self):
        source=SERVER.replace("  if text=='sample':", "  if text=='batched-sample':\n   outbound=[{'jsonrpc':'2.0','id':'sample-request','method':'sampling/createMessage','params':{}},{'jsonrpc':'2.0','id':msg['id'],'result':{'content':[{'type':'text','text':'batched-refusal'}]}}]\n   sys.stdout.write(''.join(json.dumps(frame)+'\\n' for frame in outbound));sys.stdout.flush()\n   reply=json.loads(sys.stdin.readline());assert reply.get('error',{}).get('code')==-32601\n   continue\n  if text=='sample':")
        self.script.write_text(source);self.connect()
        self.assertEqual(self.call('batched-sample')['untrusted_server_result']['content'][0]['text'],'batched-refusal')
        self.assertEqual(self.call('after-refusal')['untrusted_server_result']['content'][0]['text'],'after-refusal')
