"""Offline tests of policy, compatibility, spending, and automation boundaries."""
import copy
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from contextlib import redirect_stdout
from unittest.mock import MagicMock, patch

from codine.agent import Agent, SYSTEM
from codine.budget import Budget, BudgetError
from codine.cli import main
from codine.config import APP_ROOT, Config, Redactor
from codine.events import JsonTerminal
from codine.instructions import load_instructions
from codine.provider import OpenRouterProvider
from codine.sessions import Session, SessionStore, validate_messages
from codine.tools import build_registry
from codine.workspace import Workspace


def tool(name='list_files', args=None):
    return {'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'a', 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args or {})}}]}


class Provider:
    def __init__(self, replies, usage=None):
        self.replies = iter(replies)
        self.requests = []
        self.usage = usage

    def complete(self, messages, schemas, model, emit):
        self.requests.append(copy.deepcopy(messages))
        reply = next(self.replies)
        if isinstance(reply, BaseException):
            raise reply
        return reply, 'tool_calls' if reply.get('tool_calls') else 'stop', self.usage


class FoundationsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.store = SessionStore(self.root)
        self.addCleanup(self.store.close)
        self.session = Session.create(self.root, 'offline', SYSTEM)
        self.config = Config(self.root)
        self.ui = JsonTerminal(Redactor())
        self.ws = Workspace(self.root, lambda _: True, lambda _: None)
        self.events = []

    def agent(self, provider):
        return Agent(self.config, provider, build_registry(self.ws, lambda: None), self.store, self.session,
                     lambda *args: self.events.append(args), Redactor())

    def test_plan_filters_schema_and_dispatch_refuses_mutations(self):
        self.ws.mode = 'plan'
        registry = build_registry(self.ws, lambda: self.fail('restart must be blocked'))
        self.assertEqual({item['function']['name'] for item in registry.schemas()}, {'read_file','list_files','search','check_python','job_status','cancel_job'})
        for name, args in [('write_file', {'path':'new.py','content':'bad'}), ('run_command', {'argv':['python','-c','print(1)']}), ('replace_lines', {'path':'a.py','start_line':1,'end_line':1,'new_text':'x','expected_sha256':'a'*64}), ('request_restart', {})]:
            result = registry.execute(tool(name,args)['tool_calls'][0])
            self.assertIn('disabled', result)
        self.assertFalse((self.root/'new.py').exists())

    def test_plan_still_blocks_hallucinated_tool_in_agent_loop(self):
        self.ws.mode = 'plan'
        provider = Provider([tool('write_file', {'path':'a.py','content':'bad'}), {'role':'assistant','content':'Planning only'}])
        self.assertEqual(self.agent(provider).run_turn('plan'), 'completed')
        self.assertIn('disabled', self.session.messages[-2]['content'])
        self.assertFalse((self.root/'a.py').exists())

    def test_instructions_order_reload_and_request_context(self):
        (self.root/'AGENTS.md').write_text('Use pytest.')
        (self.root/'CODINE.md').write_text('Keep functions small.')
        provider = Provider([{'role':'assistant','content':'ok'}, {'role':'assistant','content':'ok'}])
        agent = self.agent(provider)
        agent.run_turn('task')
        first = provider.requests[0][0]['content']
        self.assertLess(first.index('Use pytest.'), first.index('Keep functions small.'))
        (self.root/'CODINE.md').write_text('Use unittest instead.')
        agent.run_turn('task two')
        self.assertIn('Use unittest instead.', provider.requests[1][0]['content'])
        self.assertNotIn('Keep functions small.', provider.requests[1][0]['content'])

    def test_instructions_reject_symlink_and_oversize(self):
        (self.root/'AGENTS.md').symlink_to(APP_ROOT/'README.md')
        with self.assertRaises(ValueError): load_instructions(self.root)
        (self.root/'AGENTS.md').unlink()
        (self.root/'AGENTS.md').write_text('a'*16001)
        with self.assertRaises(ValueError): load_instructions(self.root)

    def test_loop_guard_stops_after_three_matching_results(self):
        provider = Provider([tool(),tool(),tool(),{'role':'assistant','content':'should not run'}])
        agent = self.agent(provider)
        self.assertEqual(agent.run_turn('repeat'), 'loop_limit')
        self.assertEqual(len(provider.requests),3)
        validate_messages(self.store.load(self.session.id).messages)
        self.assertEqual(self.events[-1][1]['outcome'], 'loop_limit')

    def test_step_limit_is_not_completed(self):
        self.config.max_steps = 1
        self.assertEqual(self.agent(Provider([tool()])).run_turn('task'), 'step_limit')

    def test_budget_unknown_request_survives_resume_and_blocks_retry(self):
        self.config.provider = self.session.provider = 'openrouter'
        self.config.api_key = 'offline-test-key'
        provider = Provider([RuntimeError('connection lost')])
        agent = self.agent(provider)
        with self.assertRaises(RuntimeError): agent.run_turn('task')
        restored = self.store.load(self.session.id)
        self.assertEqual(restored.billing['pending'], '0.25')
        agent.session = restored
        with self.assertRaisesRegex(BudgetError, 'unknown'): agent.run_turn('retry')
        self.assertEqual(len(provider.requests),1)
        Budget(restored.billing).settle('0.01')
        agent.save()
        self.assertEqual(self.store.load(self.session.id).billing['spent'], '0.01')

    def test_actual_cost_and_opaque_reasoning_roundtrip(self):
        self.config.provider = self.session.provider = 'openrouter'
        self.config.api_key = 'offline-key'
        first = tool()
        first['reasoning_details'] = [{'type':'reasoning.encrypted','data':'opaque','signature':'abc'}]
        provider = Provider([first,{'role':'assistant','content':'done'}], SimpleNamespace(cost=0.01, prompt_tokens=4,completion_tokens=2,total_tokens=6))
        self.agent(provider).run_turn('task')
        self.assertEqual(self.session.billing['spent'], '0.02')
        self.assertIsNone(self.session.billing['pending'])
        self.assertEqual(provider.requests[1][-2]['reasoning_details'],first['reasoning_details'])

    def test_missing_cost_retains_reservation_and_does_not_execute(self):
        self.config.provider = self.session.provider = 'openrouter'
        self.config.api_key = 'offline-key'
        provider = Provider([tool('write_file',{'path':'no.py','content':'x'})],SimpleNamespace(total_tokens=1))
        with self.assertRaisesRegex(BudgetError,'omitted'): self.agent(provider).run_turn('task')
        self.assertFalse((self.root/'no.py').exists())
        self.assertIsNotNone(self.store.load(self.session.id).billing['pending'])

    def test_budget_invalid_costs_limit_and_overrun(self):
        for value in ('NaN','Infinity','-1',True,None):
            budget=Budget.create('1','0.25'); budget.reserve()
            with self.assertRaises(BudgetError): budget.settle(value)
            self.assertEqual(budget.state['pending'],'0.25')
        budget=Budget.create('0.30','0.25'); budget.reserve(); budget.settle('0.20')
        with self.assertRaisesRegex(BudgetError,'cannot cover'): budget.reserve()
        budget=Budget.create('1','0.25'); budget.reserve()
        self.assertTrue(budget.settle('0.50'))
        with self.assertRaises(BudgetError): budget.reserve()

    def test_openrouter_request_has_no_nvidia_parameters(self):
        self.config.api_key='mock-key'
        message=MagicMock()
        message.model_dump.return_value={'role':'assistant','content':'hello','reasoning_details':[{'type':'reasoning.encrypted','data':'opaque'}]}
        response=SimpleNamespace(choices=[SimpleNamespace(message=message,finish_reason='stop')],usage=SimpleNamespace(cost=0.001))
        with patch('codine.provider.OpenAI') as client:
            client.return_value.chat.completions.create.return_value=response
            provider=OpenRouterProvider(self.config)
            reply,_,_=provider.complete([{'role':'user','content':'hello'}],[], 'explicit-model',lambda *args:None)
            request=client.return_value.chat.completions.create.call_args.kwargs
            self.assertEqual(client.call_args.kwargs['base_url'],'https://openrouter.ai/api/v1')
            self.assertEqual(request['extra_body'],{'provider':{'require_parameters':True}})
            self.assertFalse(request['stream'])
            self.assertEqual(reply['reasoning_details'][0]['data'],'opaque')
            provider.close()

    def test_json_cli_command_no_network_and_valid_framing(self):
        out=io.StringIO()
        with patch.object(socket.socket,'connect',side_effect=AssertionError('network forbidden')), redirect_stdout(out):
            code=main(['--provider','nvidia','--project',str(self.root),'--json','--plan','--prompt','/instructions'])
        rows=[json.loads(line) for line in out.getvalue().splitlines()]
        self.assertEqual(code,0)
        self.assertEqual(rows[-1]['data']['outcome'],'command')
        self.assertEqual([r['sequence'] for r in rows],list(range(1,len(rows)+1)))

    def test_json_cli_limit_exit_code_and_tool_ids(self):
        fake=Provider([tool(),tool(),tool()])
        out=io.StringIO()
        fake.close=lambda:None
        with patch('codine.cli.NvidiaProvider',return_value=fake), redirect_stdout(out):
            code=main(['--provider','nvidia','--project',str(self.root),'--json','--prompt','repeat'])
        rows=[json.loads(line) for line in out.getvalue().splitlines()]
        self.assertEqual(code,1)
        self.assertEqual(rows[-1]['data']['outcome'],'loop_limit')
        self.assertEqual(next(r for r in rows if r['event']=='tool_request')['data']['id'],'a')
        self.assertEqual(next(r for r in rows if r['event']=='tool_response')['data']['tool_call_id'],'a')

    def test_json_restart_is_refused_without_process_replacement(self):
        out=io.StringIO()
        with patch('codine.cli.restart_process') as restart, redirect_stdout(out):
            code=main(['--provider','nvidia','--project',str(self.root),'--json','--prompt','/restart'])
        restart.assert_not_called()
        self.assertEqual(code,1)
        self.assertEqual(json.loads(out.getvalue().splitlines()[-1])['data']['outcome'],'failed')

    def test_plan_survives_explicit_restart(self):
        from codine.restart import restart_process
        agent=self.agent(Provider([]))
        received=[]
        restart_process(agent, execute=lambda executable,argv:received.append(argv), plan=True)
        self.assertIn('--plan',received[0])

    def test_project_guidance_still_requires_approval_in_auto_edit(self):
        self.ws.mode='auto-edit'
        self.ws.approve=lambda _:False
        self.assertEqual(self.ws.write_file('CODINE.md','change conventions')['status'],'declined')
        self.assertFalse((self.root/'CODINE.md').exists())

    def test_backward_compatible_session_defaults(self):
        self.store.save(self.session)
        path=self.store.path(self.session.id)
        data=json.loads(path.read_text()); data.pop('provider'); data.pop('billing')
        path.write_text(json.dumps(data))
        loaded=self.store.load(self.session.id)
        self.assertEqual(loaded.provider,'nvidia')
        self.assertEqual(loaded.billing,{})

    def test_offline_case_grader_rejects_broken_accepts_reference(self):
        from codine.evals import run_case
        task=json.loads((APP_ROOT/'evals/tasks.json').read_text())['tasks'][0]
        with patch.object(socket.socket,'connect',side_effect=AssertionError('network forbidden')):
            row=run_case(task)
        self.assertTrue(row['passed'])
        self.assertTrue(row['broken_fixture_rejected'])
        self.assertEqual(row['scripted_calls'],4)


if __name__ == '__main__': unittest.main()
