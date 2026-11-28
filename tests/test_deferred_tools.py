import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from codine.agent import Agent, PersistenceError, SYSTEM
from codine.config import Config, Redactor
from codine.context import project_context, ContextLimitError
from codine.registry import ToolRegistry, ToolSpec
from codine.sessions import Session, SessionStore, validate_messages
from codine.tools import build_registry
from codine.workspace import Workspace

SCHEMA={'type':'object','properties':{'text':{'type':'string'}},'required':['text'],'additionalProperties':False}


def call(name,args,ident='one'):
    return {'id':ident,'type':'function','function':{'name':name,'arguments':json.dumps(args)}}


def reply(*calls): return {'role':'assistant','content':None,'tool_calls':list(calls)}


class DeferredToolTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name).resolve()
        self.ws=Workspace(self.root,Mock(return_value=True),Mock())
        self.registry=build_registry(self.ws,lambda:None)
        self.handler=Mock(return_value={'ok':True})
        self.trusted=True
        self.spec=ToolSpec('ext_demo_echo','Echo from a connected integration.',copy.deepcopy(SCHEMA),self.handler,
                           effect='execute',deferred=True,availability=lambda:self.trusted)
        self.registry.register(self.spec)
        self.store=SessionStore(self.root);self.addCleanup(self.store.close)
        self.session=Session.create(self.root,'offline',SYSTEM)

    def activate(self):
        item=self.registry.catalog('ext_demo')['tools'][0]
        self.registry.request_load(item['name'],item['version'])
        self.registry.activate_pending()

    def test_real_agent_discovers_loads_then_invokes_only_next_request(self):
        requests=[]
        def complete(messages,schemas,*args):
            requests.append(copy.deepcopy(schemas))
            names={row['function']['name'] for row in schemas}
            if len(requests)==1:
                self.assertNotIn(self.spec.name,names)
                return reply(call('discover_tools',{'query':'demo'})),'tool_calls',None
            if len(requests)==2:
                row=json.loads(messages[-1]['content'])['tools'][0]
                self.assertNotIn('parameters',row)
                return reply(call('load_tool',{'name':row['name'],'version':row['version']},'load'),
                             call(self.spec.name,{'text':'too early'},'early')),'tool_calls',None
            if len(requests)==3:
                self.assertIn(self.spec.name,names)
                self.assertFalse(self.handler.called)
                self.assertIn('not loaded',messages[-1]['content'])
                return reply(call(self.spec.name,{'text':'now'},'invoke')),'tool_calls',None
            return {'role':'assistant','content':'done'},'stop',None
        provider=Mock();provider.complete.side_effect=complete
        agent=Agent(Config(self.root),provider,self.registry,self.store,self.session,Mock(),Redactor())
        self.assertEqual(agent.run_turn('Use the integration.'),'completed')
        self.handler.assert_called_once_with(text='now')
        validate_messages(self.session.messages)
        self.assertFalse(self.registry.pending)

    def test_replacement_identical_schema_invalidates_activation_and_handles(self):
        old=self.registry.catalog()['tools'][0]['version'];self.activate()
        from dataclasses import replace
        new=replace(self.spec,handler=Mock())  # Even copied revision metadata cannot reuse a handle.
        self.registry.tools[self.spec.name]=new
        self.assertNotIn(self.spec.name,{row['function']['name'] for row in self.registry.schemas()})
        with self.assertRaises(ValueError):self.registry.request_load(new.name,old)
        self.assertIn('not loaded',self.registry.execute(call(new.name,{'text':'no'})))
        new.handler.assert_not_called()
        current=self.registry.catalog()['tools'][0]
        self.assertNotEqual(old,current['version'])

    def test_in_place_handler_change_invalidates_loaded_handle(self):
        self.activate()
        previous=self.registry.catalog()['tools'][0]['version']
        self.spec.handler=Mock()
        self.assertNotIn(self.spec.name,{row['function']['name'] for row in self.registry.schemas()})
        with self.assertRaises(ValueError):self.registry.request_load(self.spec.name,previous)
        self.spec.handler.assert_not_called()

    def test_revocation_and_plan_never_activate_or_execute(self):
        row=self.registry.catalog()['tools'][0]
        self.registry.request_load(row['name'],row['version']);self.trusted=False
        self.assertTrue(self.registry.activate_pending())
        self.assertEqual(self.registry.catalog()['tools'],[])
        self.assertIn('disabled',self.registry.execute(call(self.spec.name,{'text':'no'})))
        self.trusted=True;self.activate();self.ws.mode='plan'
        self.assertEqual(self.registry.catalog()['tools'],[])
        with self.assertRaises(ValueError):self.registry.request_load(row['name'],row['version'])
        self.assertNotIn(self.spec.name,{row['function']['name'] for row in self.registry.schemas()})
        self.handler.assert_not_called()

    def test_bounded_catalog_count_schema_bytes_and_context_preflight(self):
        registry=ToolRegistry(deferred_limit=1,deferred_bytes=1024)
        for i in range(40):
            registry.register(ToolSpec('ext_'+str(i),'x'*400,copy.deepcopy(SCHEMA),Mock(),deferred=True))
        catalog=registry.catalog(limit=20)
        self.assertEqual(len(catalog['tools']),20);self.assertTrue(catalog['truncated'])
        self.assertTrue(all(len(row['description'])==240 for row in catalog['tools']))
        first,second=catalog['tools'][:2]
        registry.request_load(first['name'],first['version'])
        with self.assertRaisesRegex(ValueError,'Too many'):registry.request_load(second['name'],second['version'])
        registry.activate_pending()
        self.assertEqual(len(registry.schemas()),1)
        with self.assertRaises(ContextLimitError):
            project_context(self.session,registry.schemas(),100000,token_budget=10,output_tokens=1,allow_trim=False)
        registry.request_unload([first['name']]);registry.activate_pending()
        spec=registry.tools[second['name']];spec.description='x'*2000
        with self.assertRaisesRegex(ValueError,'byte limit'):registry.request_load(spec.name,registry.version(spec))
        self.assertEqual(registry.schemas(),[])

    def test_availability_shared_once_per_projection_never_cached_between_calls(self):
        available=Mock(return_value=True)
        registry=ToolRegistry()
        for i in range(32):
            registry.register(ToolSpec('mcp_'+str(i),'Tool',copy.deepcopy(SCHEMA),Mock(),
                                      deferred=True,availability=available))
        registry.catalog(limit=20)
        self.assertEqual(available.call_count,1)
        registry.schemas()
        self.assertEqual(available.call_count,2)
        available.return_value=False
        self.assertEqual(registry.catalog(limit=20)['tools'],[])
        self.assertEqual(available.call_count,3)

    def test_unknown_hidden_calls_no_execution_and_unload_core_refused(self):
        for name in ('unknown',self.spec.name):
            self.assertIn('error',self.registry.execute(call(name,{'text':'no'})))
        with self.assertRaises(ValueError):self.registry.request_unload(['read_file'])
        self.handler.assert_not_called()
        self.assertFalse(self.ws.approve.called)

    def test_cancel_or_save_failure_discards_pending_activation(self):
        for failure in ('cancel','save'):
            with self.subTest(failure=failure):
                row=self.registry.catalog()['tools'][0]
                provider=Mock();provider.complete.return_value=(reply(call('load_tool',{'name':row['name'],'version':row['version']})),'tool_calls',None)
                session=Session.create(self.root,'offline',SYSTEM)
                def emit(kind,data):
                    if failure=='cancel' and kind=='tool_response':raise KeyboardInterrupt()
                agent=Agent(Config(self.root),provider,self.registry,self.store,session,emit,Redactor())
                original=self.store.save
                def save(current):
                    if failure=='save' and current.pending and current.pending['results']:raise OSError('injected save fault')
                    return original(current)
                with patch.object(self.store,'save',side_effect=save):
                    with self.assertRaises(KeyboardInterrupt if failure=='cancel' else PersistenceError):agent.run_turn('Load integration.')
                self.assertFalse(self.registry.pending)
                self.assertNotIn(self.spec.name,{row['function']['name'] for row in self.registry.schemas()})
                self.handler.assert_not_called()
