import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from codine.agent import Agent
from codine.budget import Budget, BudgetError
from codine.cli import command
from codine.config import Config, Redactor
from codine.context import project_context
from codine.conversation_rewind import completed_turns, state_hash
from codine.registry import ToolRegistry
from codine.sessions import Session, SessionStore, validate_messages
from codine.shared_budget import SharedBudget
from codine.workspace import Workspace


class ConversationRewindTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name).resolve();self.file=self.root/'a.py';self.file.write_text('current file state')
        self.ws=Workspace(self.root,Mock(return_value=True),Mock())
        self.store=SessionStore(self.root);self.addCleanup(self.store.close)
        self.session=Session.create(self.root,'offline','system')
        self.session.messages += [
            {'role':'user','content':'Read original'},
            {'role':'assistant','content':None,'tool_calls':[{'id':'read1','type':'function','function':{'name':'read_file','arguments':'{"path":"a.py"}'}}]},
            {'role':'tool','tool_call_id':'read1','content':'historical file text'},
            {'role':'assistant','content':'First answer'},
            {'role':'user','content':'FUTURE_REQUEST'},
            {'role':'assistant','content':'FUTURE_ANSWER'}]
        self.session.billing=Budget.create().state;self.session.billing['spent']='0.23'
        self.session.usage={'input':100,'output':40,'total':140}
        self.session.context_start=5
        self.session.context_summary={'summary':{'constraints':['FUTURE_SUMMARY'],'decisions':[],'files':[],'tests':[],'unresolved':[]},'evidence':[]}
        self.session.summary_end=7;self.session.summary_user=5
        self.session.plan=[{'id':'future','title':'FUTURE_PLAN','status':'pending'}];self.session.plan_revision=2
        self.provider=Mock()
        self.agent=Agent(Config(self.root),self.provider,ToolRegistry(),self.store,self.session,Mock(),Redactor())
        self.ui=SimpleNamespace(interactive=False,write=Mock(),approve=Mock(return_value=True))
        self.agent.save()

    def test_historical_child_keeps_pairs_current_spend_parent_archive_and_files(self):
        original=copy.deepcopy(self.session.messages);parent_id=self.session.id
        self.ws.read_versions['a.py']='old';self.ws.skill_reads['demo']={};self.ws.notebook_reads['old']={}
        command('/rewind-context 1',self.agent,self.ws,self.ui)
        child=self.agent.session
        self.assertNotEqual(child.id,parent_id)
        self.assertEqual(child.messages,original[:5]);validate_messages(child.messages)
        self.assertEqual(child.branch['messages'],5);self.assertEqual(child.branch['parent'],parent_id)
        self.assertEqual(child.billing['spent'],'0.23');self.assertEqual(child.usage,self.session.usage)
        self.assertEqual(child.context_start,1);self.assertEqual(child.context_summary,{})
        self.assertEqual(child.summary_end,1);self.assertIsNone(child.summary_user)
        self.assertEqual(child.plan,[]);self.assertEqual(child.plan_revision,3)
        self.assertNotIn('FUTURE_',json.dumps(project_context(child,[],160000)[0]))
        self.assertEqual(self.store._read_session(parent_id).messages,original)
        self.assertEqual(self.store._read_session(parent_id).plan,self.session.plan)
        self.assertEqual(self.file.read_text(),'current file state')
        self.assertEqual(self.ws.read_versions,{});self.assertEqual(self.ws.skill_reads,{})
        self.assertEqual(self.ws.notebook_reads,{})
        self.provider.complete.assert_not_called();self.ui.approve.assert_called_once()

    def test_listing_decline_and_invalid_or_partial_turn_do_not_switch(self):
        original=self.session.id
        command('/rewind-context',self.agent,self.ws,self.ui)
        self.ui.approve.assert_not_called()
        self.assertIn('shell/network',self.ui.write.call_args_list[0].args[0])
        self.ui.approve.return_value=False
        command('/rewind-context 1',self.agent,self.ws,self.ui)
        self.assertEqual(self.agent.session.id,original)
        self.assertEqual(len(list(self.store.directory.glob('*.json'))),1)
        for argument in ('0','3','-1','1 extra','١'):
            with self.assertRaises(ValueError):command('/rewind-context '+argument,self.agent,self.ws,self.ui)
        # A recovered tool group without a final assistant message is not a completed turn.
        messages=self.session.messages[:4]
        self.assertEqual(completed_turns(messages),[])
        self.assertEqual([row['end'] for row in completed_turns(self.session.messages)], [5,7])

    def test_approval_hash_covers_context_plan_billing_and_history(self):
        for field in ('context','plan','billing','history'):
            with self.subTest(field=field):
                before=copy.deepcopy(self.session)
                def mutate(_):
                    if field=='context':self.session.context_start=1
                    elif field=='plan':self.session.plan[0]['title']='changed'
                    elif field=='billing':self.session.billing['spent']='0.24'
                    else:self.session.messages[-1]['content']='changed'
                    return True
                self.ui.approve.side_effect=mutate
                with self.assertRaisesRegex(ValueError,'state changed'):
                    command('/rewind-context 1',self.agent,self.ws,self.ui)
                self.assertEqual(self.agent.session.id,before.id)
                self.assertEqual(len(list(self.store.directory.glob('*.json'))),1)
                self.session.__dict__.update(before.__dict__)

    def test_shared_spend_preserved_and_pending_after_approval_blocks_switch(self):
        self.session.provider='openrouter';self.agent.config.provider='openrouter';self.agent.config.api_key='fake-key'
        path=self.root/'private-budget.json';self.agent.shared_budget_path=path
        ledger=SharedBudget(path,'fake-key',owner='other',request_cap='0.25')
        request=ledger.reserve();ledger.dispatched(request);ledger.settle(request,'0.12');ledger.close()
        before=path.read_bytes()
        command('/rewind-context 1',self.agent,self.ws,self.ui)
        self.assertEqual(path.read_bytes(),before)
        self.assertEqual(self.agent.session.billing['spent'],'0.23')
        current=self.agent.session.id
        def reserve(_):
            ledger=SharedBudget(path,'fake-key',owner='other',request_cap='0.25')
            request=ledger.reserve();ledger.dispatched(request);ledger.close()
            return True
        self.ui.approve.side_effect=reserve
        with self.assertRaises(BudgetError):command('/rewind-context 1',self.agent,self.ws,self.ui)
        self.assertEqual(self.agent.session.id,current)
        self.assertIsNotNone(json.loads(path.read_text())['pending'])
        self.provider.complete.assert_not_called()


class ConversationRewindTerminalTests(unittest.TestCase):
    def test_real_terminal_confirmation_creates_historical_child_without_replay(self):
        import sys
        from tests.test_terminal_integration import PTY, saved_reply
        driver=r'''
from codine import cli
class Fake:
    def __init__(self,config):self.root=config.project;self.calls=0
    def close(self):pass
    def complete(self,messages,schemas,model,emit):
        self.calls+=1
        assert self.calls<=2, 'Rewind must not call the provider'
        (self.root/'calls.txt').write_text(str(self.calls))
        text='FIRST_DONE' if self.calls==1 else 'SECOND_DONE'
        emit('text',text)
        __import__('time').sleep(.1)  # Stream visibly before committing the reply.
        return {'role':'assistant','content':text},'stop',None
cli.NvidiaProvider=Fake
raise SystemExit(cli.main(['--provider','nvidia']))
'''
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve();(root/'untouched.txt').write_text('keep current contents')
            terminal=PTY([sys.executable,'-c',driver],root)
            try:
                terminal.until('›'.encode())
                terminal.send(b'First question\r');terminal.until(b'FIRST_DONE')
                terminal.send(b'Second question\r');terminal.until(b'SECOND_DONE')
                parent=next((root/'.codine/sessions').glob('*.json'))
                original=saved_reply(parent,'SECOND_DONE')['messages']
                terminal.send(b'/rewind-context 1\r');terminal.until(b'Approve? Type yes:')
                self.assertEqual(len(list((root/'.codine/sessions').glob('*.json'))),1)
                terminal.send(b'yes\r');terminal.until(b'Conversation rewound into a new child')
                child=next(path for path in (root/'.codine/sessions').glob('*.json') if path!=parent)
                data=json.loads(child.read_text())
                self.assertEqual(data['messages'],original[:3])
                self.assertEqual(data['branch']['parent'],parent.stem)
                self.assertEqual(json.loads(parent.read_text())['messages'],original)
                self.assertEqual((root/'calls.txt').read_text(),'2')
                self.assertEqual((root/'untouched.txt').read_text(),'keep current contents')
                terminal.send(b'/quit\r');terminal.process.wait(timeout=10)
                self.assertEqual(terminal.process.returncode,0)
            finally:terminal.close()
