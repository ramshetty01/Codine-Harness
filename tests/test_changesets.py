import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from codine.changesets import ChangesetStore, apply, rewind, show
from codine.workspace import Workspace, fingerprint


class ChangesetTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.previews = []
        self.approvals = []
        self.workspace = Workspace(self.root, lambda text: self.approvals.append(text) or True, self.previews.append, track_changes=True)
        self.store = ChangesetStore(self.root)

    def put(self, path, text):
        (self.root/path).write_text(text)
        self.workspace.read_file(path)

    def change(self, path, text):
        file = self.root/path
        return {'path': path, 'content': text, 'expected_sha256': fingerprint(file.read_bytes()) if file.exists() else None}

    def test_grouped_review_and_rewind_multiple_files(self):
        self.put('a.py', 'a=1\n'); self.put('b.py', 'b=1\n')
        def approve(text):
            self.assertEqual((self.root/'a.py').read_text(), 'a=1\n')
            self.assertEqual((self.root/'b.py').read_text(), 'b=1\n')
            self.assertIn('a=2', self.previews[0]); self.assertIn('b=2', self.previews[0])
            return True
        self.workspace.approve = approve
        result = apply(self.workspace, [self.change('a.py','a=2\n'), self.change('b.py','b=2\n')])
        self.workspace.approve = lambda text: True
        self.assertEqual(result['status'], 'applied')
        self.assertEqual(rewind(self.workspace,result['changeset'])['status'],'rewound')
        self.assertEqual((self.root/'a.py').read_text(),'a=1\n')
        self.assertEqual((self.root/'b.py').read_text(),'b=1\n')

    def test_repeated_edits_and_binary_move_chain_restore_net_original(self):
        self.put('a.txt','one')
        self.workspace.edit_file('a.txt','one','two')
        self.workspace.read_file('a.txt')
        self.workspace.edit_file('a.txt','two','three')
        data=b'\0binary\xff'
        (self.root/'original.bin').write_bytes(data)
        os.chmod(self.root/'original.bin',0o751)
        sha=self.workspace.inspect_file('original.bin')['sha256']
        self.workspace.move_file('original.bin','middle.bin',sha)
        sha=self.workspace.inspect_file('middle.bin')['sha256']
        self.workspace.move_file('middle.bin','final.bin',sha)
        ident=self.workspace.change_group_id
        self.assertEqual(len(self.store.load(ident)['entries']),4)
        rewind(self.workspace,ident)
        self.assertEqual((self.root/'a.txt').read_text(),'one')
        self.assertEqual((self.root/'original.bin').read_bytes(),data)
        self.assertEqual((self.root/'original.bin').stat().st_mode & 0o777,0o751)
        self.assertFalse((self.root/'middle.bin').exists())
        self.assertFalse((self.root/'final.bin').exists())

    def test_conflict_in_last_path_preserves_every_file(self):
        self.put('a','old a'); self.put('b','old b')
        result=apply(self.workspace,[self.change('a','new a'),self.change('b','new b')])
        (self.root/'b').write_text('newer human b')
        with self.assertRaisesRegex(ValueError,'conflict'):
            rewind(self.workspace,result['changeset'])
        self.assertEqual((self.root/'a').read_text(),'new a')
        self.assertEqual((self.root/'b').read_text(),'newer human b')

    def test_approval_race_prevents_all_forward_or_reverse_changes(self):
        self.put('a','old a'); self.put('b','old b')
        def approve(text):
            if 'b?' in text: (self.root/'b').write_text('human change')
            return True
        self.workspace.approve=approve
        with self.assertRaisesRegex(ValueError,'changed'):
            apply(self.workspace,[self.change('a','new a'),self.change('b','new b')])
        self.assertEqual((self.root/'a').read_text(),'old a')
        self.assertEqual(self.store.list(),[])

    def test_partial_forward_application_recovers_all_paths(self):
        self.put('a','old a'); self.put('b','old b')
        original=self.workspace._atomic_write
        def write(target,*args,**kwargs):
            if target.name=='b': raise OSError('injected interruption')
            return original(target,*args,**kwargs)
        with patch.object(self.workspace,'_atomic_write',side_effect=write):
            with self.assertRaises(OSError):
                apply(self.workspace,[self.change('a','new a'),self.change('b','new b')])
        self.assertEqual((self.root/'a').read_text(),'new a')
        self.assertEqual((self.root/'b').read_text(),'old b')
        rewind(self.workspace,self.workspace.change_group_id)
        self.assertEqual((self.root/'a').read_text(),'old a')
        self.assertEqual((self.root/'b').read_text(),'old b')

    def test_effect_then_final_save_failure_recovers_owned_write(self):
        self.put('a','old')
        from codine import changesets
        with patch.object(changesets,'complete',side_effect=OSError('after effect')):
            with self.assertRaises(OSError): self.workspace.edit_file('a','old','new')
        self.assertEqual((self.root/'a').read_text(),'new')
        rewind(self.workspace,self.workspace.change_group_id)
        self.assertEqual((self.root/'a').read_text(),'old')

    def test_partial_lifecycle_copy_recovers_owned_prefix(self):
        (self.root/'a.bin').write_bytes(b'\0binary payload')
        sha=self.workspace.inspect_file('a.bin')['sha256']
        original=os.write; calls=0
        def short(fd,data):
            nonlocal calls
            calls+=1
            if calls==1: return original(fd,data[:2])
            raise OSError('copy interrupted')
        with patch('codine.file_lifecycle.os.write',side_effect=short):
            with self.assertRaises(OSError): self.workspace.move_file('a.bin','b.bin',sha)
        rewind(self.workspace,self.workspace.change_group_id)
        self.assertEqual((self.root/'a.bin').read_bytes(),b'\0binary payload')
        self.assertFalse((self.root/'b.bin').exists())

    def test_partial_rewind_save_failure_resumes_without_replaying_restored_path(self):
        self.put('a','old a'); self.put('b','old b')
        result=apply(self.workspace,[self.change('a','new a'),self.change('b','new b')])
        original=ChangesetStore.save; failed=False
        def save(store,record):
            nonlocal failed
            if not failed and record['entries']['a']['rewind']=='restored':
                failed=True; raise OSError('failed progress save')
            return original(store,record)
        with patch.object(ChangesetStore,'save',save):
            with self.assertRaises(OSError): rewind(self.workspace,result['changeset'])
        self.assertEqual((self.root/'a').read_text(),'old a')
        identity=(self.root/'a').stat().st_ino
        self.assertEqual((self.root/'b').read_text(),'new b')
        rewind(self.workspace,result['changeset'])
        self.assertEqual((self.root/'a').stat().st_ino,identity)
        self.assertEqual((self.root/'b').read_text(),'old b')

    def test_duplicate_aliases_permissions_and_core_preflight(self):
        self.put('a','old')
        with self.assertRaisesRegex(ValueError,'canonical'):
            apply(self.workspace,[self.change('a','new'),self.change('./a','other')])
        self.assertEqual((self.root/'a').read_text(),'old')
        self.workspace.app_root=self.root.resolve()
        self.workspace.mode='auto-edit'
        self.workspace.approve=lambda text: False
        result=apply(self.workspace,[self.change('a','new'),self.change('main.py','core')])
        self.assertEqual(result['status'],'declined')
        self.assertEqual((self.root/'a').read_text(),'old')
        self.assertFalse((self.root/'main.py').exists())
        self.workspace.mode='plan'
        with self.assertRaises(ValueError): apply(self.workspace,[self.change('a','new')])
        with self.assertRaises(ValueError): rewind(self.workspace,'0'*32)

    def test_rewind_policy_denial_and_postapproval_conflict_preserve_all(self):
        # Use real resolve path boundary for a denied path; do not bypass policies.
        self.put('a','old a'); self.put('b','old b')
        result=apply(self.workspace,[self.change('a','new a'),self.change('b','new b')])
        from codine.command_policy import CommandPolicy, FileRule
        self.workspace.command_policy=CommandPolicy(self.root/'user-policy.json',file_rules=(
            FileRule(id='deny-b',action='deny',operation='write',project=str(self.root.resolve()),match='exact',path='b'),))
        with self.assertRaisesRegex(ValueError,'policy'):
            rewind(self.workspace,result['changeset'])
        self.workspace.command_policy=None
        self.assertEqual((self.root/'a').read_text(),'new a')
        def approve(text):
            if 'Rewind b ' in text: (self.root/'b').write_text('human changed during approval')
            return True
        self.workspace.approve=approve
        with self.assertRaisesRegex(ValueError,'changed'):
            rewind(self.workspace,result['changeset'])
        self.assertEqual((self.root/'a').read_text(),'new a')

    def test_link_and_corrupt_beforeimage_fail_before_any_restore(self):
        self.put('a','old a'); self.put('b','old b')
        result=apply(self.workspace,[self.change('a','new a'),self.change('b','new b')])
        os.link(self.root/'b',self.root/'alias')
        with self.assertRaises(ValueError): rewind(self.workspace,result['changeset'])
        self.assertEqual((self.root/'a').read_text(),'new a')
        (self.root/'alias').unlink()
        record=self.store.load(result['changeset'])
        record['entries']['b']['before']['data']='corrupted base64'
        self.store.save(record)
        with self.assertRaises(ValueError): rewind(self.workspace,result['changeset'])
        self.assertEqual((self.root/'a').read_text(),'new a')

    def test_actual_agent_turn_ids_keep_two_turns_separate(self):
        from unittest.mock import Mock
        from codine.agent import Agent
        from codine.config import Config, Redactor
        from codine.sessions import Session, SessionStore
        from codine.tools import build_registry
        self.workspace.mode='auto-edit'
        session=Session.create(self.root,'offline','system')
        store=SessionStore(self.root); self.addCleanup(store.close)
        provider=Mock()
        def reply(path):
            return ({'role':'assistant','content':None,'tool_calls':[{'id':path,'type':'function','function':{'name':'write_file','arguments':json.dumps({'path':path,'content':'text'})}}]},'tool_calls',None)
        provider.complete.side_effect=[reply('a'),({'role':'assistant','content':'done'},'stop',None),reply('b'),({'role':'assistant','content':'done'},'stop',None)]
        agent=Agent(Config(self.root),provider,build_registry(self.workspace,lambda:None),store,session,lambda *args:None,Redactor())
        agent.on_turn_start=lambda ident:setattr(self.workspace,'change_group_id',ident)
        agent.run_turn('first'); first=session.last_run['id']
        agent.run_turn('second'); second=session.last_run['id']
        self.assertNotEqual(first,second)
        self.assertEqual(set(self.store.load(first)['entries']),{'a'})
        self.assertEqual(set(self.store.load(second)['entries']),{'b'})
        rewind(self.workspace,second)
        self.assertTrue((self.root/'a').exists()); self.assertFalse((self.root/'b').exists())

    def test_no_files_change_when_combined_read_coverage_or_bounds_fail(self):
        self.put('a','old')
        (self.root/'unread').write_text('must inspect')
        with self.assertRaisesRegex(ValueError,'Read every'):
            apply(self.workspace,[self.change('a','new'),self.change('unread','changed')])
        self.assertEqual((self.root/'a').read_text(),'old')
        with self.assertRaises(ValueError): apply(self.workspace,[self.change('a','new')]*33)
        with self.assertRaises(ValueError): apply(self.workspace,[self.change('.env','secret')])
        self.assertEqual(self.store.list(),[])

    def test_registered_group_tool_applies_new_files_and_is_blocked_in_plan(self):
        from codine.tools import build_registry
        registry=build_registry(self.workspace,lambda:None)
        call={'id':'group','type':'function','function':{'name':'apply_changeset','arguments':json.dumps({'changes':[self.change('new.py','x=1\n')]})}}
        result=json.loads(registry.execute(call))
        self.assertEqual(result['status'],'applied')
        self.assertEqual((self.root/'new.py').read_text(),'x=1\n')
        self.workspace.mode='plan'
        call['function']['arguments']=json.dumps({'changes':[self.change('blocked.py','bad')]})
        result=json.loads(registry.execute(call))
        self.assertIn('disabled',result['error'])
        self.assertFalse((self.root/'blocked.py').exists())
