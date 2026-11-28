import fcntl
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch

from codine.changesets import ChangesetStore,rewind
from codine.isolated_worktrees import IsolatedTrees
from codine.workspace import Workspace


class IsolationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.home=Path(self.temp.name).resolve();self.root=self.home/'project';self.root.mkdir()
        self.approve=Mock(return_value=True);self.emit=Mock()
        self.parent=Workspace(self.root,self.approve,self.emit)
        self.manager=IsolatedTrees(self.parent,self.home/'trees')
        (self.root/'a.py').write_text('a=1\n');(self.root/'b.py').write_text('b=1\n')

    def tree(self):return self.manager.allocate(['a.py','b.py'])
    def edit(self,tree,path,content): (Path(tree['path'])/path).write_text(content)

    def test_two_edit_trees_do_not_touch_parent_and_disjoint_integrations_work(self):
        first=self.tree();second=self.tree()
        self.edit(first,'a.py','a=2\n');self.edit(second,'b.py','b=2\n')
        self.assertEqual((self.root/'a.py').read_text(),'a=1\n')
        self.assertEqual((Path(second['path'])/'a.py').read_text(),'a=1\n')
        review=self.manager.review(first['id']);self.assertIn('-a=1',review['preview'])
        self.assertEqual(self.parent.read_versions,{})
        result=self.manager.integrate(review['review']);self.assertEqual(result['status'],'applied')
        review=self.manager.review(second['id']);self.manager.integrate(review['review'])
        self.assertEqual((self.root/'a.py').read_text(),'a=2\n');self.assertEqual((self.root/'b.py').read_text(),'b=2\n')
        self.assertEqual(self.approve.call_count,2)
        self.assertEqual(rewind(self.parent,result['changeset'])['status'],'rewound')
        self.assertEqual((self.root/'a.py').read_text(),'a=1\n');self.assertEqual((self.root/'b.py').read_text(),'b=2\n')

    def test_parent_conflict_in_last_file_keeps_all_files_and_untracked_destination(self):
        tree=self.tree();self.edit(tree,'a.py','a=2\n');self.edit(tree,'b.py','b=2\n')
        review=self.manager.review(tree['id'])
        (self.root/'b.py').write_text('human change\n')
        with self.assertRaisesRegex(ValueError,'conflict'):self.manager.integrate(review['review'])
        self.assertEqual((self.root/'a.py').read_text(),'a=1\n');self.approve.assert_not_called()
        fresh=self.tree();self.edit(fresh,'new.py','new=1\n')
        review=self.manager.review(fresh['id'],new_paths=['new.py'])
        (self.root/'new.py').write_text('human untracked\n')
        with self.assertRaisesRegex(ValueError,'conflict'):self.manager.integrate(review['review'])
        self.assertEqual((self.root/'new.py').read_text(),'human untracked\n')

    def test_child_change_during_approval_rejects_every_write(self):
        tree=self.tree();self.edit(tree,'a.py','a=2\n');self.edit(tree,'b.py','b=2\n')
        review=self.manager.review(tree['id'])
        def approve(_):self.edit(tree,'b.py','changed during approval\n');return True
        self.parent.approve=approve
        with self.assertRaisesRegex(ValueError,'changed since review'):self.manager.integrate(review['review'])
        self.assertEqual((self.root/'a.py').read_text(),'a=1\n')
        self.assertEqual((self.root/'b.py').read_text(),'b=1\n')

    def test_explicit_creation_and_decline_no_implicit_discovery(self):
        tree=self.tree();self.edit(tree,'new.py','new=1\n');self.edit(tree,'ignored.py','ignored=1\n')
        review=self.manager.review(tree['id'],new_paths=['new.py'])
        self.assertEqual(review['paths'],['new.py'])
        self.approve.return_value=False
        self.assertEqual(self.manager.integrate(review['review'])['status'],'declined')
        self.assertFalse((self.root/'new.py').exists())
        self.approve.return_value=True;result=self.manager.integrate(review['review'])
        self.assertEqual(result['status'],'applied');self.assertFalse((self.root/'ignored.py').exists())

    def test_hooks_filters_and_credentials_are_not_copied_or_executed(self):
        (self.root/'.git').mkdir();(self.root/'.git'/'config').write_text('[filter "evil"]\n clean = touch EXECUTED\n')
        (self.root/'.env').write_text('SECRET=private')
        (self.root/'.gitattributes').write_text('*.py filter=evil\n')
        with patch('subprocess.Popen',side_effect=AssertionError('No Git or other process may execute')):
            tree=self.tree()
        self.assertFalse((Path(tree['path'])/'.git').exists());self.assertFalse((Path(tree['path'])/'.env').exists())
        self.assertFalse((self.root/'EXECUTED').exists())
        for path in ('.env','.git/config','../outside.py'):
            with self.assertRaises(ValueError):self.manager.allocate([path])
        (self.root/'alias.py').symlink_to(self.root/'a.py')
        with self.assertRaises(ValueError):self.manager.allocate(['alias.py'])

    def test_policy_mapped_to_parent_and_receipts_unreachable(self):
        from codine.command_policy import CommandPolicy,FileRule
        self.parent.command_policy=CommandPolicy(self.home/'policy.json',file_rules=(FileRule(id='deny',action='deny',operation='write',project=str(self.root),match='exact',path='a.py'),))
        tree=self.tree();child=self.manager.workspace(tree['id'])
        child.read_file('a.py')
        with self.assertRaises(ValueError):child.edit_file('a.py','a=1','a=3')
        with self.assertRaises(ValueError):child.read_file('../base.json')
        self.edit(tree,'a.py','a=2\n')
        with self.assertRaises(ValueError):self.manager.review(tree['id'])

    def test_partial_write_failure_uses_existing_checkpoint_recovery(self):
        tree=self.tree();self.edit(tree,'a.py','a=2\n');self.edit(tree,'b.py','b=2\n')
        review=self.manager.review(tree['id'])
        original=Workspace._atomic_write
        def fail_last(workspace,target,*args,**kwargs):
            if target.name=='b.py':raise OSError('simulated disk failure')
            return original(workspace,target,*args,**kwargs)
        with patch.object(Workspace,'_atomic_write',fail_last):
            with self.assertRaises(OSError):self.manager.integrate(review['review'])
        rows=ChangesetStore(self.root).list();self.assertEqual(len(rows),1)
        self.assertEqual((self.root/'a.py').read_text(),'a=2\n');self.assertEqual((self.root/'b.py').read_text(),'b=1\n')
        self.assertEqual(rewind(self.parent,rows[0]['id'])['status'],'rewound')
        self.assertEqual((self.root/'a.py').read_text(),'a=1\n')

    def test_deleted_modes_and_busy_integration_are_explicit_failures(self):
        tree=self.tree();(Path(tree['path'])/'a.py').unlink()
        with self.assertRaisesRegex(ValueError,'Deleted'):self.manager.review(tree['id'])
        tree=self.tree();self.edit(tree,'a.py','a=2\n');review=self.manager.review(tree['id'])
        (self.root/'.codine').mkdir(exist_ok=True)
        lock=self.root/'.codine'/'integration.lock';lock.touch(mode=0o600)
        with lock.open('r+') as handle:
            fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaisesRegex(ValueError,'Another integration'):self.manager.integrate(review['review'])
        os.chmod(Path(tree['path'])/'a.py',0o700)
        with self.assertRaisesRegex(ValueError,'mode'):self.manager.review(tree['id'])

    def test_review_cannot_follow_child_links_or_tampered_private_receipts(self):
        tree=self.tree();path=Path(tree['path'])/'a.py'
        path.unlink();os.link(self.root/'a.py',path)
        with self.assertRaises(ValueError):self.manager.review(tree['id'])
        path.unlink();path.symlink_to(self.root/'a.py')
        with self.assertRaises(ValueError):self.manager.review(tree['id'])
        receipt=Path(tree['path']).parent/'base.json';receipt.chmod(0o644)
        with self.assertRaisesRegex(ValueError,'private'):self.manager.workspace(tree['id'])

    def test_planning_mode_never_integrates_and_allocation_is_bounded(self):
        with self.assertRaises(ValueError):self.manager.allocate(['a.py']*33)
        (self.root/'large.py').write_text('x'*1000001)
        with self.assertRaises(ValueError):self.manager.allocate(['large.py'])
        tree=self.tree();self.edit(tree,'a.py','a=2\n');review=self.manager.review(tree['id'])
        self.parent.mode='plan'
        with self.assertRaisesRegex(ValueError,'planning'):self.manager.integrate(review['review'])
        self.assertEqual((self.root/'a.py').read_text(),'a=1\n')
