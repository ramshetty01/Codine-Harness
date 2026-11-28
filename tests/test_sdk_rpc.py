"""Real local RPC processes with scripted providers; no network or real keys."""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest

from codine.sdk import Client, CodineError

ROOT=Path(__file__).resolve().parents[1]
BOOTSTRAP = r'''
import sys, json, time, socket
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, __PACKAGE_ROOT__)
import codine.cli as cli
from codine.config import Config
import codine.paths, codine.router_catalog
socket.create_connection=lambda *a,**k: (_ for _ in ()).throw(AssertionError('No network'))
socket.socket.connect=lambda *a,**k: (_ for _ in ()).throw(AssertionError('No network'))
codine.paths.STATE_ROOT=Path.cwd()/'.test-shared'
import codine.mcp_settings
codine.mcp_settings.MCP_ROOT=Path.cwd()/'.test-mcp'
codine.router_catalog.metadata=lambda *a: {'limit_remaining':5}
cli.Config.load=lambda project,provider=None,model=None:Config(Path(project).resolve(),provider=provider or 'nvidia',model=model or 'fake',api_key='fake-only-key')
class Fake:
    def __init__(self,config):pass
    def close(self):pass
    def complete(self,messages,schemas,model,emit):
        usage=SimpleNamespace(cost=.01,prompt_tokens=10,completion_tokens=10,total_tokens=20)
        if messages[-1]['role']=='user':
            text=messages[-1]['content']
            if text=='slow':
                while True:
                    emit('status','waiting');time.sleep(.03)
            if text=='mcp echo':
                name=next(row['function']['name'] for row in schemas if row['function']['name'].startswith('mcp_'))
                return {'role':'assistant','content':None,'tool_calls':[{'id':'mcp-call','type':'function','function':{'name':name,'arguments':json.dumps({'text':'rpc-mcp-result'})}}]},'tool_calls',usage
            if text=='unknown cost':return {'role':'assistant','content':'uncertain'},'stop',None
            if text.startswith('write '):
                args={'path':text.split(' ',1)[1],'content':'hello\n'}
                return {'role':'assistant','content':None,'tool_calls':[{'id':'call-'+str(time.time_ns()),'type':'function','function':{'name':'write_file','arguments':json.dumps(args)}}]},'tool_calls',usage
        emit('text','done')
        return {'role':'assistant','content':'done'},'stop',usage
cli.NvidiaProvider=cli.OpenRouterProvider=Fake
raise SystemExit(cli.main())
'''.replace('__PACKAGE_ROOT__',repr(str(ROOT)))


class SDKRPCTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.project=Path(temp.name).resolve()

    def client(self,**options):
        client=Client(self.project,command=[sys.executable,'-u','-c',BOOTSTRAP],**options)
        self.addCleanup(client.close)
        return client

    def test_persistent_run_status_commands_resume_and_default_deny(self):
        events=[]
        client=self.client(on_event=events.append)
        ident=client.ready['session']
        result=client.run('write denied.py')
        self.assertEqual(result['outcome']['outcome'],'completed')
        self.assertFalse((self.project/'denied.py').exists())
        self.assertTrue(any(row['event']=='approval' for row in events))
        self.assertEqual(client.status()['mode'],'ask')
        client.command('/save')
        with self.assertRaisesRegex(CodineError,'not available'):client.command('/restart')
        with self.assertRaisesRegex(CodineError,'explicit model'):client.command('/model')
        client.close()
        resumed=self.client(resume=ident)
        self.assertEqual(resumed.ready['session'],ident)
        self.assertEqual(resumed.run('continue')['outcome']['outcome'],'completed')
        self.assertFalse((self.project/'denied.py').exists())
        saved=json.loads((self.project/'.codine/sessions'/f'{ident}.json').read_text())
        self.assertEqual(len([m for m in saved['messages'] if m.get('tool_calls')]),1)

    def test_reverse_approval_writes_once_and_plan_does_not_grant(self):
        approvals=[]
        client=self.client(approve=lambda text:approvals.append(text) or True)
        client.run('write allowed.py')
        self.assertEqual((self.project/'allowed.py').read_text(),'hello\n')
        self.assertEqual(len(approvals),1)
        client.command('/plan')
        client.run('write blocked.py')
        self.assertFalse((self.project/'blocked.py').exists())
        self.assertEqual(len(approvals),1)
        client.command('/permissions ask')
        self.assertEqual(client.status()['mode'],'ask')

    def test_cancel_reaches_running_turn_and_next_turn_remains_usable(self):
        waiting=threading.Event()
        client=self.client(on_event=lambda event:waiting.set() if event['event']=='status' and event['data']=='waiting' else None)
        result=[]
        thread=threading.Thread(target=lambda:result.append(client.run('slow')))
        thread.start();self.addCleanup(lambda:thread.join(timeout=2))
        self.assertTrue(waiting.wait(5))
        self.assertTrue(client.cancel()['cancel_requested'])
        thread.join(timeout=5);self.assertFalse(thread.is_alive())
        self.assertEqual(result[0]['outcome']['outcome'],'interrupted')
        self.assertEqual(client.run('continue')['outcome']['outcome'],'completed')

    def test_rpc_uses_same_shared_cost_ledger_and_unknown_cost_blocks(self):
        client=self.client(provider='openrouter',model='fake')
        client.run('hello')
        path=self.project/'.test-shared/evals/budget.json'
        budget=json.loads(path.read_text())
        self.assertEqual(budget['spent'],'0.01')
        self.assertIsNone(budget['pending'])
        with self.assertRaises(CodineError):client.run('unknown cost')
        pending=json.loads(path.read_text())
        self.assertIsNotNone(pending['pending'])
        with self.assertRaises(CodineError):client.run('retry')
        self.assertEqual(json.loads(path.read_text())['pending'],pending['pending'])
        self.assertEqual(client.status()['cost']['spent'],'0.01')

    def test_protocol_argument_bounds_and_expired_approval(self):
        client=self.client()
        for method,params in [('run',{'text':'/permissions auto-edit'}),('command',{'text':'/resume'}),
                              ('run',{'text':'x'*16001}),('approval',{'token':'expired','allow':True}),
                              ('status',{'unexpected':True}),('unknown',{})]:
            with self.subTest(method=method,params=str(params)[:40]):
                with self.assertRaises(CodineError):client.request(method,params)
        self.assertEqual(client.run('still usable')['outcome']['outcome'],'completed')

    def test_sdk_client_trusts_connects_and_calls_real_stdio_mcp_with_approval(self):
        from tests.test_mcp import SERVER
        server=self.project/'server.py';server.write_text(SERVER)
        settings=self.project/'.test-mcp';settings.mkdir(mode=0o700)
        config=settings/'servers.json'
        config.write_text(json.dumps({'version':1,'servers':{'fixture':{
            'transport':'stdio','argv':[str(Path(sys.executable).resolve()),'-I','-u',str(server)],
            'cwd':str(self.project),'files':[str(server)],'credential_env':[]}}}))
        config.chmod(0o600)
        approvals=[];events=[]
        client=self.client(on_event=events.append,approve=lambda text:approvals.append(text) or True)
        with self.assertRaisesRegex(CodineError,'trust'):client.command('/mcp connect fixture')
        client.command('/mcp trust fixture')
        client.command('/mcp connect fixture')
        result=client.run('mcp echo')
        self.assertEqual(result['outcome']['outcome'],'completed')
        self.assertGreaterEqual(len(approvals),2)
        outputs=[str(row['data']) for row in events if row['event']=='tool_response']
        self.assertTrue(any('rpc-mcp-result' in row for row in outputs))
        client.command('/mcp disconnect fixture')
