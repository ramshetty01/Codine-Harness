from contextlib import nullcontext
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from codine.agent import Agent, SYSTEM
from codine.budget import Budget
from codine.capabilities import CapabilityError, declarations, plan_request, cached_record
from codine.config import Config, Redactor, DEFAULT_MODEL, BASE_URL
from codine.provider import OpenRouterProvider, NvidiaProvider
from codine.sessions import Session, SessionStore

TOOLS = [{'type':'function','function':{'name':'read_file','parameters':{'type':'object','properties':{}}}}]


class CapabilitiesTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name).resolve(); self.project=self.root/'project'; self.project.mkdir()
        self.config=Config(self.project, provider='openrouter', api_key='fake', max_tokens=512)

    def test_unknown_router_guard_does_not_claim_verification(self):
        plan=plan_request(self.config,'unknown',TOOLS,declared={'parameters':{'temperature':.2}})
        self.assertIsNone(plan.supported_parameters)
        self.assertIn('unknown locally',plan.note)
        self.assertEqual(plan.kwargs['tools'],TOOLS)
        self.assertEqual(plan.kwargs['extra_body']['provider'],{'require_parameters':True})
        self.assertNotIn('temperature',plan.kwargs)
        self.assertEqual(plan.omitted,('temperature',))

    def test_known_unsupported_tools_fail_locally_for_both_providers(self):
        for adapter, name in ((OpenRouterProvider,'openrouter'),(NvidiaProvider,'nvidia')):
            kwargs={'capability_record':lambda model:None} if name=='openrouter' else {}
            provider=adapter(self.config,capability_declarations=lambda project:{name:{'test':{'supported_parameters':['temperature']}}},**kwargs)
            provider.client=Mock()
            with self.assertRaises(CapabilityError): provider.complete([],TOOLS,'test',Mock())
            provider.client.chat.completions.create.assert_not_called()

    def test_model_catalog_adaptation_and_endpoint_does_not_inherit_union(self):
        record={'id':'m','supported_parameters':['tools','temperature','reasoning'], 'top_provider':{'max_completion_tokens':100}}
        declared={'parameters':{'temperature':.4,'seed':42,'reasoning':{'effort':'low'}}}
        plan=plan_request(self.config,'m',TOOLS,record=record,declared=declared)
        self.assertEqual(plan.kwargs['max_tokens'],100)
        self.assertEqual(plan.kwargs['temperature'],.4)
        self.assertNotIn('seed',plan.kwargs)
        self.assertEqual(plan.kwargs['extra_body']['reasoning'],{'effort':'low'})
        declared['endpoint']='some-provider'
        plan=plan_request(self.config,'m',TOOLS,record=record,declared=declared)
        self.assertIsNone(plan.supported_parameters)
        self.assertNotIn('temperature',plan.kwargs)
        self.assertEqual(plan.kwargs['extra_body']['provider']['only'],['some-provider'])
        self.assertFalse(plan.kwargs['extra_body']['provider']['allow_fallbacks'])
        plan=plan_request(self.config,'other',TOOLS,record=record)
        self.assertIsNone(plan.supported_parameters)
        self.assertEqual(plan.kwargs['max_tokens'],512)

    def test_nvidia_unknown_and_builtin_and_explicit_options(self):
        with self.assertRaises(CapabilityError): plan_request(self.config,'unknown',TOOLS,provider='nvidia')
        plan=plan_request(self.config,'unknown',[],provider='nvidia')
        self.assertNotIn('seed',plan.kwargs)
        self.assertNotIn('chat_template_kwargs',plan.kwargs['extra_body'])
        plan=plan_request(self.config,DEFAULT_MODEL,TOOLS,provider='nvidia')
        self.assertIn('built-in compatibility',plan.source)
        self.assertTrue(plan.kwargs['parallel_tool_calls'])
        self.assertEqual(plan.kwargs['extra_body']['chat_template_kwargs'],{'enable_thinking':True})
        declared={'endpoint':BASE_URL,'supported_parameters':['tools','temperature'],
                  'parameters':{'temperature':.6,'seed':42,'thinking':{'enabled':True}},'max_output_tokens':256}
        plan=plan_request(self.config,'custom',TOOLS,provider='nvidia',declared=declared)
        self.assertEqual(plan.kwargs['temperature'],.6)
        self.assertNotIn('seed',plan.kwargs)
        self.assertNotIn('chat_template_kwargs',plan.kwargs['extra_body'])
        self.assertEqual(plan.kwargs['max_tokens'],256)
        provider=NvidiaProvider(self.config,capability_declarations=lambda root:{'nvidia':{'custom':declared}})
        provider.client=Mock()
        provider.client.chat.completions.create.return_value=nullcontext([])
        with self.assertRaisesRegex(RuntimeError,'completion marker'): provider.complete([],TOOLS,'custom',None)
        self.assertEqual(provider.client.chat.completions.create.call_args.kwargs['temperature'],.6)

    def test_preflight_before_local_and_shared_reservations(self):
        for shared in (False,True):
            store=SessionStore(self.project); self.addCleanup(store.close)
            session=Session.create(self.project,'test',SYSTEM,provider='openrouter')
            budget=Budget.create(); session.billing=budget.state
            provider=OpenRouterProvider(self.config,capability_record=lambda model:{'id':model,'supported_parameters':[]},capability_declarations=lambda root:{})
            provider.client=Mock()
            agent=Agent(self.config,provider,Mock(),store,session,Mock(),Redactor())
            metadata=Mock(); ledger=self.root/'budget.json'
            if shared: agent.configure_shared_budget(ledger,metadata)
            with self.assertRaises(CapabilityError): agent.complete([],TOOLS,budget)
            self.assertIsNone(budget.state['pending'])
            self.assertFalse(ledger.exists())
            metadata.assert_not_called()
            provider.client.chat.completions.create.assert_not_called()
            store.close()

    def test_trusted_file_exact_scope_and_security(self):
        source=self.root/'model-capabilities.json'
        data={'version':1,'openrouter':{'m':{'supported_parameters':['tools'],'parameters':{'seed':42}}},
              'nvidia':{'n':{'endpoint':BASE_URL,'supported_parameters':['tools']}}}
        source.write_text(json.dumps(data)); source.chmod(0o600)
        self.assertEqual(declarations(self.project,source)['openrouter'],data['openrouter'])
        source.chmod(0o666)
        with self.assertRaises(CapabilityError): declarations(self.project,source)
        source.chmod(0o600)
        alias=self.root/'alias'; alias.symlink_to(source)
        with self.assertRaises(CapabilityError): declarations(self.project,alias)
        alias.unlink(); os.link(source,alias)
        with self.assertRaises(CapabilityError): declarations(self.project,source)
        alias.unlink()
        local=self.project/'model-capabilities.json'; local.write_text(json.dumps(data))
        with self.assertRaisesRegex(CapabilityError,'outside'): declarations(self.project,local)

    def test_bad_declarations_fail_without_inference(self):
        source=self.root/'model-capabilities.json'
        for spec in ({'parameters':{'seed':True}},{'endpoint':'https://evil.invalid'},
                     {'supported_parameters':'tools'},{'max_output_tokens':True},
                     {'parameters':{'reasoning':{'effort':'guess'}}},{'api_key':'fake'}):
            source.write_text(json.dumps({'version':1,'openrouter':{'m':spec}}))
            with self.subTest(spec=spec), self.assertRaises(CapabilityError): declarations(self.project,source)

    def test_stale_cache_is_unknown_and_never_refreshes(self):
        with patch('codine.catalog_cache.CatalogCache') as cache:
            cache.return_value.model_record.side_effect=ValueError('stale')
            self.assertIsNone(cached_record('m'))
            cache.return_value.model_record.assert_called_once_with('m',allow_stale=False)
            cache.return_value.refresh.assert_not_called()

    def test_prepared_plan_survives_declaration_change_without_phantom_pending(self):
        from types import SimpleNamespace
        for shared in (False, True):
            with self.subTest(shared=shared):
                store = SessionStore(self.project)
                self.addCleanup(store.close)
                session = Session.create(self.project, 'test', SYSTEM, provider='openrouter')
                budget = Budget.create(); session.billing = budget.state
                reads = Mock(side_effect=[
                    {'openrouter': {'test': {'supported_parameters': ['tools', 'temperature'], 'parameters': {'temperature': .4}}}},
                    {'openrouter': {'test': {'supported_parameters': []}}},
                ])
                provider = OpenRouterProvider(self.config, capability_record=lambda _: None, capability_declarations=reads)
                provider.client = Mock()
                provider.client.chat.completions.create.return_value = nullcontext([])
                agent = Agent(self.config, provider, Mock(), store, session, Mock(), Redactor())
                if shared:
                    agent.configure_shared_budget(self.root / 'shared.json', Mock(return_value={'limit_remaining': '5'}))
                usage = SimpleNamespace(cost='0.01', prompt_tokens=1, completion_tokens=1, total_tokens=2)
                with patch('codine.provider.collect_openrouter_stream', return_value=({'role': 'assistant', 'content': 'ok'}, 'stop', usage)):
                    agent.complete([], TOOLS, budget)
                self.assertEqual(reads.call_count, 1)
                self.assertEqual(provider.client.chat.completions.create.call_count, 1)
                self.assertEqual(provider.client.chat.completions.create.call_args.kwargs['temperature'], .4)
                self.assertIsNone(budget.state['pending'])
                self.assertEqual(budget.state['spent'], '0.01')
                # A subsequent request revalidates, rejects changed declarations,
                # and does not reserve or reuse the prior successful plan.
                with self.assertRaises(CapabilityError): agent.complete([], TOOLS, budget)
                self.assertIsNone(budget.state['pending'])
                self.assertEqual(provider.client.chat.completions.create.call_count, 1)
                store.close()

    def test_prepared_binding_matches_model_schemas_messages_and_is_immutable(self):
        import copy
        provider = OpenRouterProvider(self.config, capability_record=lambda _: None, capability_declarations=lambda _: {})
        messages = [{'role': 'user', 'content': 'original'}]
        schemas = copy.deepcopy(TOOLS)
        prepared = provider.prepare_completion(messages, schemas, 'test')
        for args in ((messages, schemas, 'other'), (messages, [], 'test'), ([], schemas, 'test')):
            with self.assertRaisesRegex(ValueError, 'does not match'): prepared.bind(*args)
        invoke = prepared.bind(messages, schemas, 'test')
        messages[0]['content'] = 'mutated after binding'
        schemas[0]['function']['name'] = 'changed_tool'
        provider.client = Mock()
        provider.client.chat.completions.create.return_value = nullcontext([])
        with patch('codine.provider.collect_openrouter_stream', return_value=('reply', 'stop', None)):
            invoke(None)
        actual = provider.client.chat.completions.create.call_args.kwargs
        self.assertEqual(actual['messages'][0]['content'], 'original')
        self.assertEqual(actual['tools'][0]['function']['name'], 'read_file')

    def test_failed_reservation_does_not_leave_reusable_plan(self):
        from codine.budget import BudgetError
        store = SessionStore(self.project); self.addCleanup(store.close)
        session = Session.create(self.project, 'test', SYSTEM, provider='openrouter')
        budget = Budget.create(); session.billing = budget.state
        budget.state['blocked'] = 'stop'
        reads = Mock(side_effect=[{'openrouter': {'test': {'supported_parameters': ['tools']}}}, {'openrouter': {'test': {'supported_parameters': []}}}])
        provider = OpenRouterProvider(self.config, capability_record=lambda _: None, capability_declarations=reads)
        provider.client = Mock()
        agent = Agent(self.config, provider, Mock(), store, session, Mock(), Redactor())
        with self.assertRaises(BudgetError): agent.complete([], TOOLS, budget)
        budget.state['blocked'] = ''
        with self.assertRaises(CapabilityError): agent.complete([], TOOLS, budget)
        provider.client.chat.completions.create.assert_not_called()
        self.assertIsNone(budget.state['pending'])

    def test_filesystem_cache_failure_becomes_unknown_without_network(self):
        with patch('codine.catalog_cache.CatalogCache') as cache:
            cache.return_value.model_record.side_effect = OSError('unreadable private cache')
            self.assertIsNone(cached_record('test'))
            plan = plan_request(self.config, 'test', TOOLS, record=cached_record('test'))
            self.assertTrue(plan.kwargs['extra_body']['provider']['require_parameters'])



if __name__=='__main__': unittest.main()
