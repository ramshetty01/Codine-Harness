import json
import os
from pathlib import Path
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import unittest
from unittest.mock import patch

from codine.config import Config
from codine.custom_endpoint import canonical_endpoint, match_session
from codine.custom_provider import CustomProvider
from codine.sessions import Session

TOOLS=[{'type':'function','function':{'name':'read_file','parameters':{'type':'object','properties':{}}}}]

class CustomProviderTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root=Path(temp.name); self.calls=[]; self.status=200; self.location=''; self.chunks=[
            {'id':'fake','choices':[{'index':0,'delta':{'content':'Hello'},'finish_reason':None}]},
            {'id':'fake','choices':[{'index':0,'delta':{},'finish_reason':'stop'}]},
            {'id':'fake','choices':[],'usage':{'prompt_tokens':3,'completion_tokens':1,'total_tokens':4,'cost':999}}]
        owner=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                owner.calls.append((self.path,self.headers.get('Authorization'),json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
                self.send_response(owner.status)
                self.send_header('Content-Type','text/event-stream')
                if owner.location:self.send_header('Location',owner.location)
                self.end_headers()
                if owner.status==200:
                    for chunk in owner.chunks:self.wfile.write(('data: '+json.dumps(chunk)+'\n\n').encode())
                    self.wfile.write(b'data: [DONE]\n\n')
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        from tests.local_network import allow_loopback
        scope=allow_loopback(server.server_address);scope.__enter__()
        self.addCleanup(scope.__exit__,None,None,None)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(lambda:(server.shutdown(),server.server_close(),thread.join()))
        self.endpoint=f'http://127.0.0.1:{server.server_port}/v1'
        self.config=Config(self.root,provider='custom',model='local',api_key='fake-custom-secret',custom_endpoint=self.endpoint,custom_unmetered=True)
    def adapter(self,declared=None):
        return CustomProvider(self.config,capability_declarations=lambda root:{'custom':{'local':declared}} if declared else {})
    def test_real_http_stream_and_immutable_prepared_routing(self):
        adapter=self.adapter(); messages=[{'role':'user','content':'Hi'}]
        ready=adapter.prepare_completion(messages,[],'local').bind(messages,[],'local')
        self.config.api_key='changed';adapter._key='changed';adapter.endpoint='https://invalid.invalid'
        reply,finish,usage=ready(None)
        self.assertEqual((reply['content'],finish),('Hello','stop'))
        self.assertEqual(usage['total_tokens'],4);self.assertNotIn('cost',usage)
        self.assertEqual(self.calls[0][:2],('/v1/chat/completions','Bearer fake-custom-secret'))
        self.assertEqual(len(self.calls),1)
    def test_redirect_auth_and_incomplete_response_not_replayed(self):
        for status in (302,401):
            self.status=status;self.location=self.endpoint+'/redirected';self.calls.clear()
            with self.assertRaises(Exception):self.adapter().complete([],[],'local',None)
            self.assertEqual(len(self.calls),1)
        self.status=200;self.chunks=self.chunks[:1];self.calls.clear()
        with self.assertRaisesRegex(RuntimeError,'completion marker'):self.adapter().complete([],[],'local',None)
        self.assertEqual(len(self.calls),1)
    def test_tools_require_exact_declared_endpoint_before_dispatch(self):
        for declared in (None,{'endpoint':self.endpoint,'supported_parameters':[]},
                         {'endpoint':self.endpoint+'/other','supported_parameters':['tools']}):
            with self.assertRaises(ValueError):self.adapter(declared).complete([],TOOLS,'local',None)
        self.assertEqual(self.calls,[])
        self.chunks=[{'id':'fake','choices':[{'index':0,'delta':{'tool_calls':[{'index':0,'id':'call1','type':'function','function':{'name':'read_file','arguments':'{'}}]},'finish_reason':None}]},
                     {'id':'fake','choices':[{'index':0,'delta':{'tool_calls':[{'index':0,'function':{'arguments':'}'}}]},'finish_reason':'tool_calls'}]}]
        reply,finish,usage=self.adapter({'endpoint':self.endpoint,'supported_parameters':['tools']}).complete([],TOOLS,'local',None)
        self.assertEqual(reply['tool_calls'][0]['function']['arguments'],'{}')
        self.assertEqual(finish,'tool_calls');self.assertIsNone(usage)
    def test_settings_authority_and_known_credentials(self):
        with patch.dict(os.environ,{},clear=True),patch('codine.settings.USER_ENV',self.root/'user.env'),patch('codine.settings.LEGACY_ENV',self.root/'legacy.env'):
            (self.root/'.env').write_text(f'CODINE_PROVIDER=custom\nCODINE_CUSTOM_ENDPOINT={self.endpoint}\nCODINE_CUSTOM_UNMETERED=true\nCODINE_CUSTOM_API_KEY=project-key\nCODINE_CUSTOM_MODEL=local\n')
            with self.assertRaises(ValueError):Config.load(self.root)
            cfg=Config.load(self.root,provider='custom',model='local',endpoint=self.endpoint,allow_unmetered_custom=True)
            self.assertEqual(cfg.api_key,'codine-local-no-auth')
            for key in ('sk-or-fake','same-fake-key'):
                with patch.dict(os.environ,{'CODINE_CUSTOM_API_KEY':key,'OPENROUTER_API_KEY':'same-fake-key'}):
                    with self.assertRaises(ValueError):Config.load(self.root,provider='custom',model='local',endpoint=self.endpoint,allow_unmetered_custom=True)
            with self.assertRaises(ValueError):Config.load(self.root,provider='custom',model='local',endpoint=self.endpoint)
    def test_url_and_session_binding(self):
        for endpoint in ('https://openrouter.ai/api/v1','https://api.openrouter.ai/v1','http://remote.invalid/v1','https://u:p@host.invalid/v1','https://host.invalid/v1?key=secret','https://host.invalid/a/../v1','https://bad_host.invalid/v1'):
            with self.assertRaises(ValueError):canonical_endpoint(endpoint)
        session=Session.create(self.root,'local','system',provider='custom',provider_endpoint=self.endpoint)
        match_session(self.config,session)
        self.config.custom_endpoint=self.endpoint+'/other'
        with self.assertRaises(ValueError):match_session(self.config,session)
    def test_cli_resume_binding_and_budget_unknown_without_dispatch(self):
        import io
        from contextlib import redirect_stdout
        from codine.cli import main
        from codine.sessions import SessionStore
        session=Session.create(self.root,'local','system',provider='custom',provider_endpoint=self.endpoint)
        store=SessionStore(self.root);store.save(session);store.close()
        with patch.dict(os.environ,{},clear=True),patch('codine.settings.USER_ENV',self.root/'absent.env'),patch('codine.settings.LEGACY_ENV',None):
            args=['--project',str(self.root),'--resume',session.id,'--endpoint',self.endpoint,'--allow-unmetered-custom','--prompt','/budget']
            with redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(args),0)
            self.assertIn('unknown',output.getvalue());self.assertEqual(self.calls,[])
            args[args.index(self.endpoint)]=self.endpoint+'/different'
            with redirect_stdout(io.StringIO()) as output:self.assertEqual(main(args),1)
            self.assertIn('endpoint differs',output.getvalue());self.assertEqual(self.calls,[])

    def test_whitespace_cannot_disguise_known_provider_credentials(self):
        for key in (' sk-or-fake', ' nvapi-fake', ' known-key', 'known-key '):
            with self.subTest(key=key):
                self.config.api_key=key
                self.config._custom_forbidden_keys=('known-key',)
                with self.assertRaises(ValueError):
                    self.adapter().complete([],[],'local',None)
        self.assertEqual(self.calls,[])

    def test_invalid_header_credentials_fail_before_client_creation(self):
        for key in ('é-key','key\x80','key\nvalue','key\tvalue','key\x7f','x'*8193,' sk-or-fake'):
            with self.subTest(length=len(key)):
                self.config.api_key=key
                with patch('codine.custom_provider.OpenAI') as client:
                    with self.assertRaises(ValueError):self.adapter().complete([],[],'local',None)
                    client.assert_not_called()
        self.assertEqual(self.calls,[])

    def test_environment_proxy_cannot_redirect_custom_request(self):
        with patch.dict(os.environ,{'HTTP_PROXY':'http://127.0.0.1:1',
                                    'HTTPS_PROXY':'http://127.0.0.1:1',
                                    'ALL_PROXY':'http://127.0.0.1:1','NO_PROXY':''}):
            reply,finish,_=self.adapter().complete([],[],'local',None)
        self.assertEqual((reply['content'],finish),('Hello','stop'))
        self.assertEqual(len(self.calls),1)
        self.assertEqual(self.calls[0][1],'Bearer fake-custom-secret')

    def test_sdk_custom_endpoint_acknowledgement_and_real_loopback_request(self):
        from codine.sdk import Client, CodineError
        with tempfile.TemporaryDirectory() as directory:
            home=Path(directory).resolve()
            settings=home/'.config/codine';settings.mkdir(parents=True,mode=0o700)
            capabilities=settings/'model-capabilities.json'
            capabilities.write_text(json.dumps({'version':1,'custom':{'local':{
                'endpoint':self.endpoint,'supported_parameters':['tools']}}}))
            capabilities.chmod(0o600)
            environment={'HOME':str(home),'PATH':os.environ.get('PATH',''),
                         'CODINE_HOME':str(home/'state'), 'CODINE_CUSTOM_UNMETERED':'true'}
            with patch.dict(os.environ,environment,clear=True):
                with self.assertRaises(CodineError):
                    Client(self.root,provider='custom',model='local',endpoint=self.endpoint,
                           allow_unmetered_custom=False)
                self.assertEqual(self.calls,[])
                with Client(self.root,provider='custom',model='local',endpoint=self.endpoint,
                            allow_unmetered_custom=True) as client:
                    result=client.run('Say hello without tools.')
                    self.assertEqual(result['outcome']['outcome'],'completed')
                    client.command('/budget')
                self.assertEqual(len(self.calls),1)
                self.assertEqual(self.calls[0][0],'/v1/chat/completions')
                self.assertEqual(self.calls[0][1],'Bearer codine-local-no-auth')
