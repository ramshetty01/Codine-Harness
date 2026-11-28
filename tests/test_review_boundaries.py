"""Integration regressions: inspection is not edit authorization or turn ownership."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from codine.attachments import Attachments
from codine.changesets import apply, rewind, ChangesetStore
from codine.config import Config, Redactor
from codine.historyview import details_command
from codine.sessions import Session, SessionStore
from codine.workspace import Workspace, fingerprint


class ReviewBoundariesTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name).resolve()
        self.ws=Workspace(self.root,lambda _:True,lambda _:None,track_changes=True)

    def test_attachment_and_history_inspection_do_not_authorize_grouped_edits(self):
        (self.root/'code.py').write_text('value=1\n')
        redactor=Redactor()
        attached=Attachments(self.ws,redactor,lambda _:None).prepare('Explain @code.py')
        self.assertIn('value=1',attached)
        session=Session.create(self.root,'offline','system')
        session.messages += [
            {'role':'assistant','content':None,'tool_calls':[{'id':'read','type':'function','function':{'name':'read_file','arguments':'{"path":"code.py"}'}}]},
            {'role':'tool','tool_call_id':'read','content':'value=1\n'}]
        agent=SimpleNamespace(session=session,redactor=redactor)
        details_command('G2.1',agent,SimpleNamespace(write=Mock()))
        self.assertEqual(self.ws.read_versions,{})
        self.assertEqual(self.ws.read_ranges,{})
        self.assertEqual(self.ws.lifecycle_versions,{})
        with self.assertRaisesRegex(ValueError,'Read every'):
            apply(self.ws,[{'path':'code.py','content':'value=2\n','expected_sha256':fingerprint((self.root/'code.py').read_bytes())}])
        self.assertEqual((self.root/'code.py').read_text(),'value=1\n')
        self.assertEqual(ChangesetStore(self.root).list(),[])

    def test_last_apply_journal_race_preserves_unrelated_work_and_blocks_whole_rewind(self):
        from codine import changesets
        changes=[]
        for name in ('a','b'):
            (self.root/name).write_text('old '+name)
            self.ws.read_file(name)
            changes.append({'path':name,'content':'new '+name,'expected_sha256':fingerprint((self.root/name).read_bytes())})
        own=changesets.own
        def record_owner(workspace,path,owner):
            own(workspace,path,owner)
            if path=='b': (self.root/'b').write_text('unrelated editor work')
        with patch.object(changesets,'own',side_effect=record_owner):
            with self.assertRaisesRegex(ValueError,'changed'):
                apply(self.ws,changes)
        self.assertEqual((self.root/'a').read_text(),'new a')
        self.assertEqual((self.root/'b').read_text(),'unrelated editor work')
        # A fresh process sees durable pending state and refuses all-or-nothing
        # claims: newer b prevents recovery from touching already-applied a.
        resumed=Workspace(self.root,lambda _:True,lambda _:None,track_changes=True)
        with self.assertRaisesRegex(ValueError,'conflict'):
            rewind(resumed,self.ws.change_group_id)
        self.assertEqual((self.root/'a').read_text(),'new a')
        self.assertEqual((self.root/'b').read_text(),'unrelated editor work')

    def test_file_appearing_during_owner_journal_is_not_overwritten(self):
        from codine import changesets
        own=changesets.own
        def record_owner(workspace,path,owner):
            own(workspace,path,owner)
            (self.root/path).write_text('appeared externally')
        with patch.object(changesets,'own',side_effect=record_owner):
            with self.assertRaisesRegex(ValueError,'appeared'):
                self.ws.write_file('new.txt','proposed')
        self.assertEqual((self.root/'new.txt').read_text(),'appeared externally')

    def test_new_and_resume_clear_turn_group_and_read_authorization(self):
        from codine.agent import Agent
        from codine.cli import command
        from codine.registry import ToolRegistry
        store=SessionStore(self.root); self.addCleanup(store.close)
        first=Session.create(self.root,'offline','system'); store.save(first)
        agent=Agent(Config(self.root),Mock(),ToolRegistry(),store,first,Mock(),Redactor())
        self.ws.change_group_id='a'*32
        self.ws.read_versions['stale']='oldhash'
        ui=SimpleNamespace(interactive=False,write=Mock())
        command('/new',agent,self.ws,ui)
        self.assertIsNone(self.ws.change_group_id)
        self.assertEqual(self.ws.read_versions,{})
        self.assertEqual(self.ws.change_session_id,agent.session.id)
        self.ws.change_group_id='b'*32
        command('/resume '+first.id,agent,self.ws,ui)
        self.assertIsNone(self.ws.change_group_id)
        self.assertEqual(self.ws.change_session_id,first.id)

    def test_import_starts_without_old_turn_or_file_permissions(self):
        from codine.agent import Agent
        from codine.registry import ToolRegistry
        from codine.transcripts import command
        store=SessionStore(self.root); self.addCleanup(store.close)
        session=Session.create(self.root,'offline','system'); store.save(session)
        agent=Agent(Config(self.root),Mock(),ToolRegistry(),store,session,Mock(),Redactor())
        ui=SimpleNamespace(interactive=True,write=Mock())
        command('export','history.json',agent,self.ws,ui,'system')
        command('export','confirm '+agent._transcript_review['digest'],agent,self.ws,ui,'system')
        self.ws.change_group_id='c'*32
        self.ws.read_versions['old']='hash'
        self.ws.lifecycle_versions['old']=('identity','hash')
        command('import','history.json',agent,self.ws,ui,'system')
        command('import','confirm '+agent._transcript_review['digest'],agent,self.ws,ui,'system')
        self.assertNotEqual(agent.session.id,session.id)
        self.assertIsNone(self.ws.change_group_id)
        self.assertEqual(self.ws.read_versions,{})
        self.assertEqual(self.ws.lifecycle_versions,{})
        self.assertEqual(self.ws.change_session_id,agent.session.id)
