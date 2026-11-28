import copy
from types import SimpleNamespace
from unittest.mock import Mock
import unittest
from codine.cache_evals import measure
from codine.cache_metrics import measurement,describe
from codine.usage import record,validate
from tests.test_conversation_rewind import ConversationRewindTests
from codine.cli import command


class CacheTests(unittest.TestCase):
    def test_missing_zero_and_inconsistent_counts(self):
        self.assertIsNone(measurement(None)['cache_hit'])
        self.assertFalse(measurement({'prompt_tokens_details':{'cached_tokens':0}})['cache_hit'])
        self.assertIsNone(measurement({'prompt_tokens':2,'prompt_tokens_details':{'cached_tokens':3}})['cache_read_fraction'])
        state={};record(state,{'prompt_tokens':10});validate(state)
        for section in ('last','totals','reported'):state[section].pop('cache_write')
        validate(state);self.assertIn('cache_write: last unknown',describe(state))
        record(state,{'prompt_tokens_details':{'cache_write_tokens':0}});validate(state)
        self.assertEqual(state['reported']['cache_write'],1)
        self.assertEqual(state['totals']['cache_write'],0)
    def test_repeats_identical_copies_abort_failures_and_never_execute_tools(self):
        seen=[]
        def complete(messages,schemas,model,emit):
            seen.append(copy.deepcopy(messages));messages[0]['content']='mutated'
            return {'content':'done'},'stop',{'prompt_tokens':10,'prompt_tokens_details':{'cached_tokens':5}}
        report=measure(complete,[{'role':'user','content':'same'}],[],'fake',clock=iter([0,1,2,3]).__next__)
        self.assertEqual(seen[0],seen[1]);self.assertEqual(report['results'][1]['cache_read_fraction'],.5)
        failing=Mock(side_effect=RuntimeError('uncertain'))
        with self.assertRaises(RuntimeError):measure(failing,[],[],'fake')
        self.assertEqual(failing.call_count,1)
        with self.assertRaises(ValueError):measure(lambda *args:({'tool_calls':[{}]},'tool_calls',None),[],[],'fake')


class FallbackTests(ConversationRewindTests):
    def test_review_confirm_preserves_archive_billing_and_clears_active_state(self):
        original=copy.deepcopy(self.session.messages);parent=self.session.id
        self.session.messages[-1]['reasoning_details']=[{'type':'reasoning.encrypted','data':'opaque'}]
        original=copy.deepcopy(self.session.messages);self.agent.save()
        command('/fallback review next-model',self.agent,self.ws,self.ui)
        digest=self.agent._fallback_review[0]
        self.assertEqual(self.agent.session.id,parent)
        command('/fallback confirm '+digest,self.agent,self.ws,self.ui)
        child=self.agent.session
        self.assertNotEqual(child.id,parent);self.assertEqual(child.messages,original)
        self.assertEqual(child.context_start,len(original));self.assertEqual(child.summary_end,1)
        self.assertEqual(child.plan,[]);self.assertEqual(child.context_summary,{})
        self.assertEqual(child.model,'next-model');self.assertEqual(child.billing['spent'],'0.23')
        self.assertEqual(self.store._read_session(parent).messages,original)
        self.provider.complete.assert_not_called()
    def test_changed_review_pending_and_direct_reasoning_switch_blocked(self):
        command('/fallback review next-model',self.agent,self.ws,self.ui)
        digest=self.agent._fallback_review[0];self.session.title='changed'
        with self.assertRaises(ValueError):command('/fallback confirm '+digest,self.agent,self.ws,self.ui)
        self.session.billing['pending']='0.1'
        with self.assertRaises(ValueError):command('/fallback review next-model',self.agent,self.ws,self.ui)
        self.session.billing['pending']=None
        self.session.messages[-1]['reasoning']='plain reasoning may be model-specific'
        with self.assertRaisesRegex(ValueError,'model-bound'):command('/model next-model',self.agent,self.ws,self.ui)
    def test_shared_unknown_charge_blocks_review(self):
        from codine.shared_budget import SharedBudget
        from codine.budget import BudgetError
        self.session.provider='openrouter';self.agent.config.provider='openrouter'
        self.agent.config.api_key='fake-key';self.agent.shared_budget_path=self.root/'shared.json'
        with SharedBudget(self.agent.shared_budget_path,'fake-key',owner='fake-owner') as budget:
            budget.reserve()
        with self.assertRaises(BudgetError):command('/fallback review next-model',self.agent,self.ws,self.ui)
        self.assertFalse(hasattr(self.agent,'_fallback_review'))

    def test_custom_fallback_keeps_endpoint_and_excludes_opaque_archive_from_request(self):
        from codine.context import project_context
        endpoint='https://compatible.example.test/v1'
        self.session.provider=self.agent.config.provider='custom'
        self.session.provider_endpoint=self.agent.config.custom_endpoint=endpoint
        self.agent.config.custom_unmetered=True
        self.session.messages[-1]['reasoning_details']=[{'type':'reasoning.encrypted','data':'old-model-opaque'}]
        self.agent.save()
        command('/fallback review next-model',self.agent,self.ws,self.ui)
        command('/fallback confirm '+self.agent._fallback_review[0],self.agent,self.ws,self.ui)
        child=self.agent.session
        self.assertEqual(child.provider_endpoint,endpoint)
        self.assertEqual(self.store._read_session(child.id).provider_endpoint,endpoint)
        self.assertIn('old-model-opaque',str(child.messages))
        self.assertNotIn('old-model-opaque',str(project_context(child,[],160000)[0]))
        self.assertEqual(child.billing['spent'],'0.23')
        self.provider.complete.assert_not_called()

    def test_shared_settled_receipts_unchanged_and_confirm_rechecks_preflight_mutation(self):
        from codine.shared_budget import SharedBudget
        self.session.provider=self.agent.config.provider='openrouter'
        self.agent.config.api_key='fake-key';self.agent.shared_budget_path=self.root/'shared.json'
        with SharedBudget(self.agent.shared_budget_path,'fake-key',owner='fake-owner') as budget:
            budget.reserve('a'*32);budget.dispatched('a'*32);budget.settle('a'*32,'0.23')
        before=self.agent.shared_budget_path.read_bytes()
        command('/fallback review next-model',self.agent,self.ws,self.ui)
        digest=self.agent._fallback_review[0]
        self.provider.preflight.side_effect=lambda *args:setattr(self.session,'title','changed during confirm')
        with self.assertRaisesRegex(ValueError,'changed'):
            command('/fallback confirm '+digest,self.agent,self.ws,self.ui)
        self.assertIs(self.agent.session,self.session)
        self.assertEqual(self.agent.shared_budget_path.read_bytes(),before)
        self.provider.preflight.side_effect=None
        command('/fallback review next-model',self.agent,self.ws,self.ui)
        command('/fallback confirm '+self.agent._fallback_review[0],self.agent,self.ws,self.ui)
        self.assertEqual(self.agent.session.billing['spent'],'0.23')
        self.assertEqual(self.agent.shared_budget_path.read_bytes(),before)
        self.provider.complete.assert_not_called()

class FallbackPolicyTests(unittest.TestCase):
    def test_private_policy_and_workspace_protection(self):
        import json
        from pathlib import Path
        import tempfile
        from codine.fallback import policy, USER_FALLBACKS
        from codine.workspace import Workspace
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve();project=root/'project';project.mkdir();source=root/'fallbacks.json'
            source.write_text(json.dumps({'version':1,'providers':{'openrouter':['vendor/a']}}));source.chmod(0o600)
            self.assertEqual(policy(project,source)['openrouter'],['vendor/a'])
            source.chmod(0o666)
            with self.assertRaises(ValueError):policy(project,source)
            self.assertIn(USER_FALLBACKS.resolve(),Workspace(project,Mock(),Mock()).protected_paths)
