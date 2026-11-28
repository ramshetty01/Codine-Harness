import json
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

from codine.agent import Agent
from codine.artifacts import ArtifactStore
from codine.changes import ChangeStore
from codine.config import Config,Redactor
from codine.registry import ToolRegistry
from codine.sessions import Session,SessionStore
from codine.storage import Storage
from codine.storage_lock import mutation
from codine.workspace import Workspace


class StorageTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name).resolve();self.ws=Workspace(self.root,Mock(return_value=True),Mock())
        self.store=SessionStore(self.root);self.addCleanup(self.store.close)
        self.session=Session.create(self.root,'fake','system')
        self.agent=Agent(Config(self.root),Mock(),ToolRegistry(),self.store,self.session,Mock(),Redactor())
        self.agent.save();self.clock=time.time()+100
        self.storage=Storage(self.agent,self.ws,clock=lambda:self.clock)
        self.emit=Mock();self.approve=Mock(return_value=True)

    def old(self,path):os.utime(path,(self.clock-90*86400,)*2)
    def other_session(self):
        store=SessionStore(self.root)
        session=Session.create(self.root,'fake','system')
        store.save(session);path=store.path(session.id);store.close();self.old(path)
        return session,path
    def artifact(self,text,old=True):
        ident=ArtifactStore(self.root).put('read_file',text);path=self.root/'.codine/artifacts'/f'{ident}.json'
        if old:self.old(path)
        return ident,path

    def test_counts_retention_and_referenced_artifacts_are_preserved(self):
        old_session,path=self.other_session()
        referenced,refpath=self.artifact('referenced output')
        orphan,orphanpath=self.artifact('orphan output')
        newest,_=self.artifact('keep newest',old=False)
        self.session.messages += [{'role':'user','content':'artifact '+referenced},{'role':'assistant','content':'seen'}]
        self.agent.save()
        inspect=self.storage.inspect()
        self.assertEqual(inspect['categories']['sessions']['files'],2)
        self.assertEqual(inspect['categories']['artifacts']['bytes'],sum(p.stat().st_size for p in (self.root/'.codine/artifacts').glob('*.json')))
        plan=self.storage.plan(30,1,16)
        self.assertEqual({row['id'] for row in plan['selected']},{old_session.id,orphan})
        result=self.storage.apply(plan['digest'],self.approve,self.emit)
        self.assertEqual(result['disk_bytes_reclaimed'],0)
        self.assertFalse(path.exists());self.assertFalse(orphanpath.exists());self.assertTrue(refpath.exists())
        batch=self.root/'.codine/storage-trash'/result['batch']
        self.assertTrue((batch/('sessions--'+old_session.id+'.json')).exists())
        self.assertEqual(json.loads((batch/'manifest.json').read_text())['status'],'complete')
        self.assertGreater(self.storage.inspect()['categories']['retained_trash']['bytes'],0)
        self.assertTrue(self.store.path(self.session.id).exists())

    def test_changed_or_declined_plan_never_moves_records(self):
        _,path=self.other_session();plan=self.storage.plan(30,1,16)
        self.assertEqual(self.storage.apply(plan['digest'],lambda _:False,self.emit)['status'],'declined')
        self.assertTrue(path.exists())
        def change(_):
            data=json.loads(path.read_text());data['title']='changed';path.write_text(json.dumps(data));return True
        with self.assertRaisesRegex(ValueError,'changed'):self.storage.apply(plan['digest'],change,self.emit)
        self.assertTrue(path.exists())
        self.assertFalse((self.root/'.codine/storage-trash').exists())

    def test_any_other_live_session_prevents_cleanup_not_only_candidates(self):
        self.other_session()
        other=SessionStore(self.root);self.addCleanup(other.close)
        live=Session.create(self.root,'fake','system');other.save(live)
        with self.assertRaisesRegex(ValueError,'Another project session'):self.storage.plan(30,1,16)
        other.close()
        self.assertTrue(self.storage.plan(30,1,16)['selected'])

    def test_mutation_lock_blocks_all_publication_and_new_session_acquisition(self):
        from codine.changesets import ChangesetStore
        from codine.inbox import Inbox
        inbox=Inbox(self.store,self.session)
        with mutation(self.root,exclusive=True):
            for call in (lambda:self.agent.save(),lambda:ArtifactStore(self.root).put('read_file','blocked'),
                         lambda:ChangeStore(self.root).prepare('a.py',b'before','a'*64),
                         lambda:ChangesetStore(self.root).save({'id':'a'*32}),
                         lambda:inbox.add('queued'),lambda:SessionStore(self.root).acquire('new-session')):
                with self.assertRaises((OSError,RuntimeError)):call()
        self.assertTrue(inbox.cancel.is_set())
        self.agent.save()

    def test_active_checkpoints_pending_sessions_and_queue_are_protected(self):
        session,path=self.other_session()
        data=json.loads(path.read_text());data['billing']={'limit':'3.50','request_cap':'0.25','spent':'0','pending':'0.25','blocked':False}
        # Use the real budget shape to keep ordinary session validation authoritative.
        from codine.budget import Budget
        data['billing']=Budget.create().state;data['billing']['pending']='0.25'
        path.write_text(json.dumps(data));self.old(path)
        record=ChangeStore(self.root).prepare('a.py',b'old','a'*64)
        checkpoint=ChangeStore(self.root)._path(record['id']);self.old(checkpoint)
        newer=ChangeStore(self.root).prepare('b.py',b'new','b'*64)
        plan=self.storage.plan(30,1,16)
        self.assertNotIn(session.id,{row['id'] for row in plan['selected']})
        self.assertNotIn(record['id'],{row['id'] for row in plan['selected']})
        inbox=path.with_suffix('.inbox');inbox.write_text('[{"text":"pending"}]');inbox.chmod(0o600)
        with self.assertRaisesRegex(ValueError,'queue'):self.storage.plan(30,1,16)

    def test_only_obsolete_unreferenced_beforeimages_eligible(self):
        store=ChangeStore(self.root)
        old=store.prepare('a.py',b'old','a'*64);old['undone']=True;store.save(old);self.old(store._path(old['id']))
        retained=store.prepare('b.py',b'old','b'*64,session_id=self.session.id);retained['undone']=True;store.save(retained);self.old(store._path(retained['id']))
        store.prepare('new.py',b'new','c'*64)
        plan=self.storage.plan(30,1,16)
        self.assertIn(old['id'],{row['id'] for row in plan['selected']})
        self.assertNotIn(retained['id'],{row['id'] for row in plan['selected']})

    def test_unsafe_permissions_links_and_changed_during_move_fail_closed(self):
        _,path=self.other_session();path.chmod(0o644)
        with self.assertRaisesRegex(ValueError,'Unsafe'):self.storage.plan(30,1,16)
        path.chmod(0o600)
        plan=self.storage.plan(30,1,16)
        from codine.rename_noreplace import rename_noreplace
        def race(source,destination,**kwargs):
            data=json.loads(path.read_text());data['title']='racing change';path.write_text(json.dumps(data))
            return rename_noreplace(source,destination,**kwargs)
        with patch('codine.storage.rename_noreplace',side_effect=race):
            with self.assertRaisesRegex(ValueError,'retained'):self.storage.apply(plan['digest'],self.approve,self.emit)
        batches=list((self.root/'.codine/storage-trash').iterdir());self.assertEqual(len(batches),1)
        moved=next(p for p in batches[0].iterdir() if p.name.startswith('sessions--'))
        self.assertEqual(json.loads(moved.read_text())['title'],'racing change')
        self.assertEqual(json.loads((batches[0]/'manifest.json').read_text())['status'],'pending')
        with self.assertRaisesRegex(ValueError,'trash'):self.storage.plan(30,1,16)

    def test_no_budget_trust_config_or_package_state_scanned(self):
        for relative in ('.env','.codine/evals/budget.json','.codine/packages/receipts.json','.codine/trust.json'):
            path=self.root/relative;path.parent.mkdir(exist_ok=True,parents=True);path.write_text('not JSON; never read')
        self.other_session()
        plan=self.storage.plan(30,1,16)
        self.assertEqual({row['category'] for row in plan['selected']},{'sessions'})
        self.storage.apply(plan['digest'],self.approve,self.emit)
        self.assertEqual((self.root/'.env').read_text(),'not JSON; never read')

    def test_other_process_session_lease_blocks_plan_then_releases(self):
        import subprocess,sys
        from codine.config import APP_ROOT
        self.other_session()
        script="""from pathlib import Path
import sys
from codine.sessions import Session,SessionStore
root=Path(sys.argv[1]);store=SessionStore(root)
session=Session.create(root,'fake','system');store.save(session)
print('locked',flush=True);sys.stdin.read();store.close()
"""
        child=subprocess.Popen([sys.executable,'-c',script,str(self.root)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE,text=True,cwd=APP_ROOT)
        try:
            self.assertEqual(child.stdout.readline().strip(),'locked')
            with self.assertRaisesRegex(ValueError,'Another project session'):self.storage.plan(30,1,16)
        finally:child.communicate('',timeout=10)
        self.assertEqual(child.returncode,0)
        self.assertTrue(self.storage.plan(30,1,16)['selected'])

    def test_new_reference_during_approval_invalidates_plan(self):
        orphan,path=self.artifact('old orphan');self.artifact('new',old=False)
        plan=self.storage.plan(30,1,16)
        self.assertIn(orphan,{row['id'] for row in plan['selected']})
        def reference(_):
            self.session.messages += [{'role':'user','content':'Read artifact '+orphan},{'role':'assistant','content':'okay'}]
            self.agent.save();return True
        with self.assertRaisesRegex(ValueError,'changed'):self.storage.apply(plan['digest'],reference,self.emit)
        self.assertTrue(path.exists())

    def test_age_keep_byte_bounds_and_symlinks_refused(self):
        for args in ((0,1,1),(1,0,1),(1,1,0),(1,1,65)):
            with self.assertRaises(ValueError):self.storage.plan(*args)
        artifact,path=self.artifact('x'*1100000);self.artifact('new',old=False)
        self.assertNotIn(artifact,{row['id'] for row in self.storage.plan(30,1,1)['selected']})
        self.assertIn(artifact,{row['id'] for row in self.storage.plan(30,1,2)['selected']})
        moved=path.with_suffix('.backup');path.rename(moved);path.symlink_to(moved)
        with self.assertRaises(ValueError):self.storage.plan(30,1,2)
        self.assertTrue(moved.exists())

    def test_fork_parent_retention_keeps_child_provenance_honest(self):
        from codine.session_forks import tree
        parent,path=self.other_session()
        from codine.session_forks import history_hash
        self.session.branch={'version':1,'project':str(self.root),'parent':parent.id,'root':parent.id,'depth':1,
                             'messages':1,'history_sha256':history_hash(self.session.messages)}
        self.agent.save()
        plan=self.storage.plan(30,1,16);self.storage.apply(plan['digest'],self.approve,self.emit)
        view=tree(self.store,self.session.id)
        current=next(row for row in view['sessions'] if row['id']==self.session.id)
        self.assertTrue(current['parent_missing']);self.assertEqual(current['parent'],parent.id)

    def test_trash_session_references_keep_artifact_after_parent_archiving(self):
        reference,refpath=self.artifact('referenced by old session');self.artifact('new',old=False)
        session,path=self.other_session()
        data=json.loads(path.read_text());data['messages'] += [{'role':'user','content':reference},{'role':'assistant','content':'seen'}]
        path.write_text(json.dumps(data));self.old(path)
        plan=self.storage.plan(30,1,16)
        self.assertNotIn(reference,{row['id'] for row in plan['selected']})
        self.storage.apply(plan['digest'],self.approve,self.emit)
        self.assertFalse(path.exists())
        following=self.storage.plan(30,1,16)
        self.assertNotIn(reference,{row['id'] for row in following['selected']})
        self.assertTrue(refpath.exists())

    def test_returned_plan_mutation_cannot_redirect_cleanup_and_plan_mode_blocks_apply(self):
        _,path=self.other_session();plan=self.storage.plan(30,1,16)
        plan['selected'][0]['path']='.env'
        secret=self.root/'.env';secret.write_text('fake secret left untouched')
        self.ws.mode='plan'
        with self.assertRaisesRegex(ValueError,'plan mode'):self.storage.apply(plan['digest'],self.approve,self.emit)
        self.ws.mode='ask'
        self.storage.apply(plan['digest'],self.approve,self.emit)
        self.assertEqual(secret.read_text(),'fake secret left untouched');self.assertFalse(path.exists())
