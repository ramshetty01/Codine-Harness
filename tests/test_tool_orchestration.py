import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from codine.agent import Agent,SYSTEM
from codine.artifacts import ArtifactStore,register_artifact_tool
from codine.config import Config,Redactor
from codine.registry import ToolRegistry,ToolSpec
from codine.sessions import Session,SessionStore,validate_messages
from codine.tool_orchestration import ExternalLimits


def call(name,ident,args=None):return {'id':ident,'type':'function','function':{'name':name,'arguments':json.dumps(args or {})}}
def reply(calls):return {'role':'assistant','content':None,'tool_calls':calls}


class OrchestrationTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name).resolve();self.store=SessionStore(self.root);self.addCleanup(self.store.close)
        self.session=Session.create(self.root,'offline',SYSTEM)
        self.registry=ToolRegistry();self.handlers=[]
        for name in ('external_a','external_b','external_c','external_d'):
            handler=Mock(return_value={'rows':[{'id':i,'noise':'x'*200} for i in range(100)]})
            self.handlers.append(handler)
            self.registry.register(ToolSpec(name,'External',{'type':'object','properties':{},'additionalProperties':False},handler,effect='execute',deferred=True))
        for row in self.registry.catalog()['tools']:self.registry.request_load(row['name'],row['version'])
        self.registry.activate_pending()
        self.provider=Mock()
        self.agent=Agent(Config(self.root),self.provider,self.registry,self.store,self.session,Mock(),Redactor())
        self.agent.artifact_store=ArtifactStore(self.root)
        register_artifact_tool(self.registry,self.agent.artifact_store,self.agent)

    def test_multi_external_results_project_artifact_without_repeating_calls(self):
        def complete(messages,*_):
            if self.provider.complete.call_count==1:
                return reply([call('external_a','a'),call('external_b','b')]),'tool_calls',None
            if self.provider.complete.call_count==2:
                first=json.loads(messages[-2]['content'])
                self.assertIn('artifact_id',first)
                return reply([call('project_tool_result','projection',{'artifact_id':first['artifact_id'],
                                  'pointer_path':'/rows','fields':['/id'],'limit':3})]),'tool_calls',None
            result=json.loads(messages[-1]['content'])
            self.assertEqual(result['value'],[{'/id':0},{'/id':1},{'/id':2}])
            self.assertTrue(result['truncated'])
            self.assertEqual(result['source']['source_tool'],'external_a')
            return {'role':'assistant','content':'Compared the results.'},'stop',None
        self.provider.complete.side_effect=complete
        self.assertEqual(self.agent.run_turn('Compare two external sources.'),'completed')
        self.handlers[0].assert_called_once();self.handlers[1].assert_called_once()
        validate_messages(self.session.messages)

    def test_failure_decline_or_mcp_error_skips_rest_of_batch_then_allows_replan(self):
        for failure in ({'error':'provider failed'},{'status':'declined'}, {'untrusted_server_result':{'isError':True}}):
            with self.subTest(failure=failure):
                self.session.messages=self.session.messages[:1]
                for handler in self.handlers:handler.reset_mock()
                self.handlers[0].return_value=failure
                core=Mock(return_value='must not run')
                self.registry.tools['core_write']=ToolSpec('core_write','Core',{'type':'object'},core,effect='write')
                self.provider.complete.side_effect=[(reply([call('external_a','a'),call('external_b','b'),call('core_write','c')]),'tool_calls',None),
                                                    ({'role':'assistant','content':'Replanned after failure.'},'stop',None)]
                self.assertEqual(self.agent.run_turn('Do controlled actions.'),'completed')
                self.handlers[0].assert_called_once();self.handlers[1].assert_not_called();core.assert_not_called()
                results=[json.loads(row['content']) for row in self.session.messages if row['role']=='tool']
                self.assertEqual([row['status'] for row in results[1:]],['skipped','skipped'])
                self.assertTrue(all(not row['action_executed'] for row in results[1:]))
                validate_messages(self.session.messages)

    def test_external_caps_preserve_pairs_and_return_distinct_paused_outcome(self):
        self.agent.external_limits=ExternalLimits(per_batch=2,per_turn=3)
        self.provider.complete.side_effect=[(reply([call('external_a','a'),call('external_b','b'),call('external_c','c')]),'tool_calls',None),
                                            (reply([call('external_c','d'),call('external_d','e')]),'tool_calls',None)]
        self.assertEqual(self.agent.run_turn('Work across sources.'),'tool_limit')
        self.assertEqual(sum(handler.call_count for handler in self.handlers),3)
        self.handlers[3].assert_not_called()
        saved=self.store._read_session(self.session.id)
        self.assertEqual(saved.last_run['outcome'],'tool_limit')
        self.assertEqual(saved.last_run['state'],'paused');self.assertFalse(saved.last_run['retryable'])
        self.assertIsNone(saved.pending);validate_messages(saved.messages)
        results=[json.loads(row['content']) for row in saved.messages if row['role']=='tool']
        self.assertEqual(results[2]['status'],'skipped');self.assertEqual(results[-1]['status'],'skipped')

    def test_cancellation_preserves_known_result_and_never_starts_later_action(self):
        self.provider.complete.return_value=(reply([call('external_a','a'),call('external_b','b')]),'tool_calls',None)
        def emit(kind,data):
            if kind=='tool_response':raise KeyboardInterrupt()
        self.agent.emit=emit
        with self.assertRaises(KeyboardInterrupt):self.agent.run_turn('Work across sources.')
        self.handlers[0].assert_called_once();self.handlers[1].assert_not_called()
        validate_messages(self.session.messages)
        result=next(row for row in self.session.messages if row.get('tool_call_id')=='a')
        self.assertIn('artifact_id',json.loads(result['content']))
        self.assertIsNone(self.session.pending)

    def test_freeform_failure_words_do_not_infer_structured_failure(self):
        self.handlers[0].return_value='The report contains the words failed and declined.'
        self.provider.complete.side_effect=[(reply([call('external_a','a'),call('external_b','b')]),'tool_calls',None),
                                            ({'role':'assistant','content':'done'},'stop',None)]
        self.agent.run_turn('Read both reports.')
        self.handlers[1].assert_called_once()
