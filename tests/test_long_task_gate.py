"""Combined offline pipeline gate; scripted summaries are not model-quality evidence."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from codine.agent import Agent
from codine.config import Config, Redactor
from codine.registry import ToolRegistry, ToolSpec
from codine.sessions import Session, SessionStore, validate_messages


class LongTaskGate(unittest.TestCase):
    def test_multiple_compactions_interrupted_batch_and_durable_resume(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary).resolve()
            target=root/'greeting.py'
            target.write_text('def greet(name):\n    return "wrong"\n')
            requirement='Preserve greet(name); return Hello, {name}!; add no dependencies.'
            config=Config(root,provider='openrouter',api_key='offline-only',context_chars=10000,
                          context_tokens=20000,max_tokens=500,max_steps=20)
            session=Session.create(root,'offline','Follow the user task.',provider='openrouter')
            registry=ToolRegistry();effects=[];requests=[];summaries=[];normal=0;resuming=False
            def check():
                run=subprocess.run([sys.executable,'-I','-c',
                    'import runpy; m=runpy.run_path("greeting.py"); assert m["greet"]("Ram")=="Hello, Ram!"'],
                    cwd=root,capture_output=True,text=True,timeout=5)
                return {'path':'greeting.py','sha256':hashlib.sha256(target.read_bytes()).hexdigest(),
                        'exit_code':run.returncode,'passed':run.returncode==0}
            initial=check();self.assertFalse(initial['passed'])
            def read_evidence():
                return {**initial,'investigation_step':normal,'content':target.read_text(),'diagnostic':'bounded investigation '*140}
            def checkpoint():effects.append('checkpoint');return {'status':'checkpoint_saved'}
            def interrupted_write():
                effects.append('write')
                target.write_text('def greet(name):\n    return f"Hello, {name}!"\n')
                raise KeyboardInterrupt()
            for name,handler,effect in [('investigate',read_evidence,'read'),('checkpoint',checkpoint,'write'),
                    ('interrupted_write',interrupted_write,'write'),('never_run',lambda:effects.append('NEVER'),'write'),
                    ('verify',check,'read')]:
                registry.register(ToolSpec(name,'Investigation '+('s'*1700 if name=='investigate' else name),
                                           {'type':'object','properties':{},'additionalProperties':False},handler,effect=effect))
            def calls(names):
                return {'role':'assistant','content':None,'tool_calls':[
                    {'id':f'{normal}-{name}','type':'function','function':{'name':name,'arguments':'{}'}} for name in names]}
            def complete(messages,schemas,*args):
                nonlocal normal
                validate_messages(messages)
                requests.append(copy.deepcopy(messages))
                usage=SimpleNamespace(cost=0.01,prompt_tokens=40,completion_tokens=10,total_tokens=50)
                if not schemas:
                    payload=json.loads(messages[1]['content']);summaries.append(payload)
                    self.assertIn('Preserve greet(name)',json.dumps(payload))
                    result={'constraints':[requirement],'decisions':['Inspect durable evidence before retrying writes'],
                            'files':['greeting.py'], 'tests':['Initial check failed; no successful verification yet'],
                            'unresolved':['Verify actual current bytes; never replay unknown actions']}
                    return {'role':'assistant','content':json.dumps(result)},'stop',usage
                normal+=1
                if not resuming:
                    return calls(['investigate'] if normal<=6 else ['checkpoint','interrupted_write','never_run']),'tool_calls',usage
                if messages[-1].get('role')!='tool' or '"passed": true' not in messages[-1].get('content',''):
                    self.assertIn('outcome unknown',json.dumps(messages))
                    return calls(['verify']),'tool_calls',usage
                return {'role':'assistant','content':'The explicitly rerun check passed. No interrupted action was replayed.'},'stop',usage
            provider=Mock();provider.complete.side_effect=complete
            store=SessionStore(root)
            agent=Agent(config,provider,registry,store,session,lambda *args:None,Redactor())
            ledger=root/'test-allowance/budget.json'
            agent.configure_shared_budget(ledger,read_metadata=lambda *args:{'limit_remaining':5})
            try:
                with self.assertRaises(KeyboardInterrupt):agent.run_turn(requirement)
                self.assertGreaterEqual(len(summaries),2)
                self.assertEqual(effects,['checkpoint','write'])
                archived=copy.deepcopy(session.messages)
                before_cost=session.billing['spent'];before_count=len(requests)
                before_shared=json.loads(ledger.read_text())
                ident=session.id
            finally:store.close()
            store=SessionStore(root)
            try:
                restored=store.load(ident)
                self.assertEqual(restored.messages,archived)
                self.assertEqual(restored.billing['spent'],before_cost)
                self.assertEqual(len(requests),before_count)
                self.assertEqual(effects,['checkpoint','write'])
                validate_messages(restored.messages)
                results=[m for m in restored.messages if m['role']=='tool']
                self.assertIn('checkpoint_saved',results[-3]['content'])
                self.assertTrue(all('outcome unknown' in m['content'] for m in results[-2:]))
                self.assertIn('"passed": false',json.dumps(restored.context_summary['evidence']))
                self.assertIn(initial['sha256'],json.dumps(restored.context_summary['evidence']))
                resuming=True
                agent=Agent(config,provider,registry,store,restored,lambda *args:None,Redactor())
                agent.configure_shared_budget(ledger,read_metadata=lambda *args:{'limit_remaining':5})
                self.assertEqual(agent.run_turn('Inspect and verify current bytes; do not replay the interrupted write.'),'completed')
                final=store.load(ident)
                self.assertEqual(effects,['checkpoint','write'])
                self.assertEqual(final.messages[:len(archived)],archived)
                self.assertIsNone(final.pending);validate_messages(final.messages)
                self.assertEqual(final.usage_details['requests'],len(requests))
                from decimal import Decimal
                summary_cost=Decimal('0.01')*len(summaries)
                task_cost=Decimal('0.01')*normal
                self.assertGreaterEqual(summary_cost,Decimal('0.02'))
                self.assertEqual(len(requests),len(summaries)+normal)
                self.assertEqual(Decimal(final.billing['spent']),summary_cost+task_cost)
                shared=json.loads(ledger.read_text())
                self.assertEqual(shared['spent'],final.billing['spent'])
                self.assertGreater(Decimal(shared['spent']),Decimal(before_shared['spent']))
                self.assertIsNone(shared['pending'])
                self.assertEqual(len(shared['records']),len(requests))
                self.assertTrue(all(row['phase']=='settled' and Decimal(row['cost'])==Decimal('0.01')
                                    for row in shared['records'].values()))
                self.assertTrue(check()['passed'])
                self.assertIn(requirement,json.dumps(final.messages))
            finally:store.close()
