import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from codine.agent import Agent
from codine.budget import Budget, BudgetError
from codine.cli import command
from codine.config import Config, Redactor
from codine.context import project_context
from codine.inbox import Inbox
from codine.registry import ToolRegistry
from codine.session_forks import fork, tree, history_hash, validate_branch
from codine.sessions import Session, SessionStore, validate_messages
from codine.shared_budget import SharedBudget
from codine.workspace import Workspace


class SessionForkTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.ws = Workspace(self.root, Mock(return_value=True), Mock())
        self.store = SessionStore(self.root); self.addCleanup(self.store.close)
        self.session = Session.create(self.root, 'offline', 'system')
        self.session.messages += [
            {'role':'user','content':'Read a.py'},
            {'role':'assistant','content':None,'tool_calls':[{'id':'read1','type':'function','function':{'name':'read_file','arguments':'{"path":"a.py"}'}}]},
            {'role':'tool','tool_call_id':'read1','content':'old file content'},
            {'role':'assistant','content':'Finished.'}]
        self.session.billing = Budget.create().state
        self.session.billing['spent'] = '0.10'
        self.session.usage = {'input':10,'output':5,'total':15}
        self.provider = Mock()
        self.agent = Agent(Config(self.root), self.provider, ToolRegistry(), self.store, self.session, Mock(), Redactor())
        self.agent.save()
        self.ui = SimpleNamespace(interactive=False, write=Mock())

    def shared(self):
        self.agent.session.provider = 'openrouter'; self.agent.config.provider = 'openrouter'
        self.agent.config.api_key = 'offline-key'
        self.agent.shared_budget_path = self.root / 'private-budget.json'
        ledger = SharedBudget(self.agent.shared_budget_path, 'offline-key', owner='test', request_cap='0.25')
        return ledger

    def test_fork_tip_preserves_pairs_context_summary_spend_and_parent_archive(self):
        self.session.context_summary = {'summary': {'constraints':['user constraint'], 'decisions':[], 'files':[], 'tests':[], 'unresolved':[]}, 'evidence':[]}
        self.session.summary_end = len(self.session.messages)
        self.session.summary_user = 1
        self.session.plan = [{'id':'task','title':'More work','status':'pending'}]
        self.agent.save()
        old_messages = copy.deepcopy(self.session.messages)
        old_projection = project_context(self.session, [], 160000)[0]
        parent_id = self.session.id
        self.ws.read_versions['a.py'] = 'old'; self.ws.skill_reads['project:demo'] = {'sha256':'a'*64}
        command('/fork Alternative approach', self.agent, self.ws, self.ui)
        child = self.agent.session
        self.assertNotEqual(child.id, parent_id)
        self.assertEqual(child.title, 'Alternative approach')
        self.assertEqual(child.messages, old_messages)
        self.assertEqual(project_context(child, [], 160000)[0], old_projection)
        self.assertEqual(child.billing, self.session.billing)
        self.assertEqual(child.usage, self.session.usage)
        self.assertEqual(child.plan, self.session.plan)
        self.assertEqual(child.branch['history_sha256'], history_hash(old_messages))
        self.assertEqual(child.branch['parent'], parent_id)
        self.assertEqual(child.branch['root'], parent_id)
        self.assertEqual(self.ws.read_versions, {}); self.assertEqual(self.ws.skill_reads, {})
        self.assertEqual(child.queue_receipts, []); self.assertEqual(child.last_run, {})
        child.messages += [{'role':'user','content':'Only in the child'}, {'role':'assistant','content':'Child answer'}]
        self.agent.save()
        self.assertEqual(self.store._read_session(parent_id).messages, old_messages)
        command('/tree ' + parent_id, self.agent, self.ws, self.ui)
        self.assertEqual(self.agent.session.id, parent_id)
        self.assertEqual(self.agent.session.messages, old_messages)
        self.provider.complete.assert_not_called()
        validate_messages(child.messages)
        view = tree(self.store, parent_id)
        self.assertEqual(len(view['sessions']), 2)
        self.assertTrue(next(row for row in view['sessions'] if row['id']==parent_id)['active'])

    def test_old_version_and_fork_provenance_validation(self):
        data = json.loads(self.store.path(self.session.id).read_text()); data.pop('branch')
        self.assertEqual(self.store._read_session(self.session.id, payload=data).branch, {})
        child = fork(self.agent, self.ws)
        for key, value in [('project','/other'), ('parent',child.id), ('depth',True), ('depth',33),
                           ('messages',3), ('history_sha256','bad'), ('root','../invalid')]:
            branch = copy.deepcopy(child.branch); branch[key] = value
            with self.assertRaises(ValueError): validate_branch(branch, child)
        # Source hash records fork-time bytes; later credential redaction is not
        # treated as an attempt to change parent provenance or replay history.
        child.messages[1]['content'] = 'newly known secret'
        self.agent.redactor = Redactor(['newly known secret'])
        self.store.redactor = self.agent.redactor
        self.agent.save()
        self.assertNotIn('newly known secret', json.dumps(self.store._read_session(child.id).messages))

    def test_pending_inflight_queue_jobs_and_local_budget_block_before_new_session(self):
        before = set(self.store.directory.glob('*.json'))
        for change in ('pending','running','billing','inflight','jobs','queue'):
            with self.subTest(change=change):
                self.agent._in_turn = change == 'inflight'
                self.session.pending = {'reply': {}} if change == 'pending' else None
                self.session.last_run = {'outcome':'running'} if change == 'running' else {}
                self.session.billing['pending'] = '0.25' if change == 'billing' else None
                self.ws.jobs = SimpleNamespace(status=lambda:[{'status':'running'}] if change=='jobs' else [])
                if change == 'queue':
                    self.agent.inbox = Inbox(self.store, self.session); self.agent.inbox.add('Queued user request')
                with self.assertRaises((ValueError,BudgetError)): fork(self.agent, self.ws)
                self.assertEqual(set(self.store.directory.glob('*.json')), before)
                self.assertEqual(self.agent.session.id, self.session.id)
        self.provider.complete.assert_not_called()

    def test_shared_spending_and_global_pending_not_reset_or_bypassed(self):
        ledger = self.shared()
        request = ledger.reserve(); ledger.dispatched(request); ledger.settle(request, '0.20'); ledger.close()
        path = self.agent.shared_budget_path; original = path.read_bytes()
        child = fork(self.agent, self.ws)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(child.billing['spent'], '0.10')
        ledger = SharedBudget(path, 'offline-key', owner='different-session', request_cap='0.25')
        request = ledger.reserve(); ledger.dispatched(request); ledger.close()
        pending = path.read_bytes()
        with self.assertRaisesRegex(BudgetError, 'shared spending'): fork(self.agent, self.ws)
        self.assertEqual(path.read_bytes(), pending)
        self.assertEqual(self.agent.session.id, child.id)

    def test_no_shared_allowance_created_for_unused_key_and_local_only_refused(self):
        self.agent.session.provider = 'openrouter'; self.agent.config.provider = 'openrouter'
        self.agent.session.billing['spent'] = '0'
        with self.assertRaisesRegex(BudgetError,'shared spending'): fork(self.agent,self.ws)
        path = self.root / 'absent-budget.json'
        self.agent.shared_budget_path = path
        fork(self.agent,self.ws)
        self.assertFalse(path.exists()); self.assertFalse(path.with_suffix('.lock').exists())

    def test_failed_child_save_preserves_parent_lock_and_active_session(self):
        old_id = self.session.id
        original = SessionStore.save
        def fail_child(store, session):
            if session.id != old_id: raise OSError('injected child write failure')
            return original(store, session)
        with patch.object(SessionStore,'save',fail_child):
            with self.assertRaises(OSError): fork(self.agent,self.ws)
        self.assertEqual(self.agent.session.id,old_id)
        self.assertEqual(self.store.active,old_id)
        competitor=SessionStore(self.root); self.addCleanup(competitor.close)
        with self.assertRaisesRegex(ValueError,'already open'): competitor.load(old_id)

    def test_tree_bounds_missing_parent_project_identity_and_depth(self):
        child=fork(self.agent,self.ws)
        self.store.path(self.session.id).unlink()
        view=tree(self.store,child.id)
        self.assertTrue(view['sessions'][0]['parent_missing'])
        foreign=json.loads(self.store.path(child.id).read_text()); foreign['project']='/another-project'
        (self.store.directory/'foreign.json').write_text(json.dumps(foreign))
        (self.store.directory/'oversized.json').write_bytes(b'x'*2_000_001)
        view=tree(self.store,child.id)
        self.assertEqual(len(view['sessions']),1); self.assertTrue(view['truncated'])
        child.branch['depth']=32
        self.agent.save()
        with self.assertRaisesRegex(ValueError,'depth'): fork(self.agent,self.ws)
        self.assertEqual(self.agent.session.id,child.id)

    def test_durable_queue_without_attached_inbox_refuses_fork(self):
        inbox = Inbox(self.store, self.session)
        inbox.add('Deferred durable request')
        self.assertFalse(hasattr(self.agent, 'inbox'))
        with self.assertRaisesRegex(ValueError, 'queued input'): fork(self.agent, self.ws)
        self.assertEqual(self.agent.session.id, self.session.id)
        self.assertEqual(self.agent.inbox.list()[0]['text'], 'Deferred durable request')

    def test_identity_collision_and_locked_target_never_overwrite(self):
        with patch('codine.session_forks.Session.create', return_value=self.session):
            with self.assertRaises(ValueError): fork(self.agent, self.ws)
        self.assertEqual(self.store._read_session(self.session.id).messages, self.session.messages)
        other = Session.create(self.root, 'offline', 'other system')
        separate = SessionStore(self.root); self.addCleanup(separate.close)
        separate.save(other)
        child_bytes = separate.path(other.id).read_bytes()
        with patch('codine.session_forks.Session.create', return_value=other):
            with self.assertRaisesRegex(ValueError, 'already open'): fork(self.agent, self.ws)
        self.assertEqual(separate.path(other.id).read_bytes(), child_bytes)
        self.assertEqual(self.store.active, self.session.id)
        separate.close()
        with patch('codine.session_forks.Session.create', return_value=other):
            with self.assertRaisesRegex(ValueError, 'collision'): fork(self.agent, self.ws)
        self.assertEqual(separate.path(other.id).read_bytes(), child_bytes)

    def test_forked_session_migration_clears_foreign_tree_but_preserves_origin(self):
        from codine.migration import migrate
        import shutil
        child = fork(self.agent, self.ws)
        self.agent.save(); self.store.close()
        relocated = tempfile.TemporaryDirectory(); self.addCleanup(relocated.cleanup)
        target = Path(relocated.name).resolve() / 'project'
        shutil.copytree(self.root, target)
        preview = migrate(target, child.id, str(self.root), system='new system')
        result = migrate(target, child.id, str(self.root), system='new system', confirmation=preview['confirmation'])
        destination = SessionStore(target); self.addCleanup(destination.close)
        moved = destination.load(result['new_session_id'])
        self.assertEqual(moved.branch, {})
        self.assertEqual(moved.migration['source_id'], child.id)
        self.assertEqual(moved.migration['source_project'], str(self.root))
        self.assertEqual(moved.billing['spent'], child.billing['spent'])


class SessionForkTerminalTests(unittest.TestCase):
    def test_terminal_fork_tree_navigation_preserves_parent_without_model_replay(self):
        import sys
        from tests.test_terminal_integration import PTY
        driver = r'''
from codine import cli
class Fake:
    def __init__(self, config): self.root=config.project; self.calls=0
    def close(self): pass
    def complete(self,messages,schemas,model,emit):
        self.calls += 1
        (self.root/'calls.txt').write_text(str(self.calls))
        assert self.calls <= 2, 'Navigation must not call a provider'
        if self.calls == 2:
            assert any(m.get('content')=='Parent question' for m in messages)
            assert messages[-1]['content']=='Child question'
        text='PARENT_COMPLETE' if self.calls == 1 else 'CHILD_COMPLETE'
        emit('text',text)
        return {'role':'assistant','content':text}, 'stop', None
cli.NvidiaProvider=Fake
raise SystemExit(cli.main(['--provider','nvidia']))
'''
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve()
            terminal=PTY([sys.executable,'-c',driver],root)
            try:
                terminal.until('›'.encode())
                terminal.send(b'Parent question\r'); terminal.until(b'PARENT_COMPLETE')
                parent_path=next((root/'.codine/sessions').glob('*.json'))
                parent_id=parent_path.stem
                parent_history=json.loads(parent_path.read_text())['messages']
                terminal.send(b'/fork Alternative\r'); terminal.until(b'Forked complete tip history')
                child_path=next(p for p in (root/'.codine/sessions').glob('*.json') if p != parent_path)
                self.assertEqual(json.loads(child_path.read_text())['branch']['parent'], parent_id)
                self.assertEqual((root/'calls.txt').read_text(),'1')
                terminal.send(b'Child question\r'); terminal.until(b'CHILD_COMPLETE')
                self.assertEqual(json.loads(parent_path.read_text())['messages'],parent_history)
                offset=len(terminal.output)
                terminal.send(b'/tree\r'); terminal.until(b'Conversation tree',start=offset)
                terminal.until(child_path.stem.encode(),start=offset)
                offset=len(terminal.output)
                terminal.send(('/tree '+parent_id+'\r').encode())
                terminal.until(('Active session: '+parent_id).encode(),start=offset)
                self.assertEqual((root/'calls.txt').read_text(),'2')
                self.assertEqual(json.loads(parent_path.read_text())['messages'],parent_history)
                terminal.send(b'/quit\r'); terminal.process.wait(timeout=10)
                self.assertEqual(terminal.process.returncode,0)
            finally:
                terminal.close()
