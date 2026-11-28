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

    def test_busy_integration_is_an_explicit_failure(self):
        tree=self.tree();self.edit(tree,'a.py','a=2\n');review=self.manager.review(tree['id'])
        (self.root/'.codine').mkdir(exist_ok=True)
        lock=self.root/'.codine'/'integration.lock';lock.touch(mode=0o600)
        with lock.open('r+') as handle:
            fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaisesRegex(ValueError,'Another integration'):self.manager.integrate(review['review'])

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

    def git_fixture(self):
        from codine.git_worktrees import command
        command(self.root,['init'])
        command(self.root,['add','a.py','b.py'])
        command(self.root,['-c','user.name=Fixture','-c','user.email=fixture@example.invalid','-c','commit.gpgSign=false','commit','-m','base'])
        return command

    def test_real_linked_worktrees_pin_revision_without_checkout_hooks_or_filters(self):
        command=self.git_fixture()
        marker=self.home/'executed'
        hook=self.root/'.git/hooks/post-checkout';hook.write_text('#!/bin/sh\ntouch '+str(marker)+'\n');hook.chmod(0o755)
        command(self.root,['config','filter.evil.clean','touch '+str(marker)])
        command(self.root,['config','filter.evil.smudge','touch '+str(marker)])
        command(self.root,['config','filter.evil.process','touch '+str(marker)])
        command(self.root,['config','filter.evil.required','true'])
        (self.root/'.gitattributes').write_text('*.py filter=evil\n')
        (self.root/'.env').write_text('NEVER_COPY=secret')
        first=self.manager.allocate(['a.py'],linked=True)
        second=self.manager.allocate(['b.py'],linked=True)
        self.assertEqual(first['kind'],'linked_git_worktree')
        self.assertEqual(first['base_revision'],second['base_revision'])
        self.assertTrue((Path(first['path'])/'.git').is_file())
        self.assertFalse((Path(first['path'])/'b.py').exists())
        self.assertFalse((Path(first['path'])/'.env').exists())
        self.assertFalse(marker.exists())
        self.edit(first,'a.py','a=3\n');review=self.manager.review(first['id'])
        self.manager.integrate(review['review'])
        self.assertEqual((self.root/'a.py').read_text(),'a=3\n')
        self.assertEqual(self.manager.cleanup(first['id'])['status'],'cleaned')
        self.assertFalse(Path(first['path']).exists())
        self.assertTrue(Path(second['path']).exists())
        self.assertEqual(command(self.root,['rev-parse','HEAD']).decode().strip(),first['base_revision'])
        self.assertFalse(marker.exists())
        self.manager.cleanup(second['id'])

    def test_linked_creation_failure_has_explicit_cleanup_recovery(self):
        self.git_fixture()
        from codine.git_worktrees import attach
        def fail_after_attach(*args):attach(*args);raise OSError('simulated failure')
        with patch('codine.git_worktrees.attach',side_effect=fail_after_attach):
            with self.assertRaisesRegex(ValueError,'incomplete'):self.manager.allocate(['a.py'],linked=True)
        ident=next(self.manager.root.iterdir()).name
        self.assertEqual(self.manager._load(ident,require_tree=False)['status'],'allocating')
        self.assertEqual(self.manager.cleanup(ident)['status'],'cleaned')
        self.assertEqual(self.manager.cleanup(ident)['status'],'cleaned')

    def test_linked_metadata_change_refuses_cleanup(self):
        self.git_fixture();tree=self.manager.allocate(['a.py'],linked=True)
        pointer=Path(tree['path'])/'.git';original=pointer.read_bytes();pointer.write_bytes(original+b'\n')
        with self.assertRaisesRegex(ValueError,'metadata changed'):self.manager.cleanup(tree['id'])
        self.assertTrue(Path(tree['path']).exists());pointer.write_bytes(original)
        self.manager.cleanup(tree['id'])

    def test_rename_delete_and_modes_integrate_as_one_reversible_group(self):
        tree=self.tree();root=Path(tree['path'])
        (root/'a.py').rename(root/'renamed.py');(root/'b.py').unlink();(root/'renamed.py').chmod(0o755)
        review=self.manager.review(tree['id'],new_paths=['renamed.py'])
        self.assertEqual(set(review['paths']),{'a.py','b.py','renamed.py'})
        self.assertIn('absent',review['preview']);self.assertIn('0o755',review['preview'])
        result=self.manager.integrate(review['review'])
        self.assertFalse((self.root/'a.py').exists());self.assertFalse((self.root/'b.py').exists())
        self.assertEqual((self.root/'renamed.py').read_text(),'a=1\n')
        self.assertEqual((self.root/'renamed.py').stat().st_mode&0o777,0o755)
        rewind(self.parent,result['changeset'])
        self.assertFalse((self.root/'renamed.py').exists());self.assertEqual((self.root/'a.py').read_text(),'a=1\n')
        self.assertEqual((self.root/'b.py').read_text(),'b=1\n')

    def test_mode_only_change_is_previewed_and_rewind_restores_mode(self):
        tree=self.tree();(Path(tree['path'])/'a.py').chmod(0o755)
        review=self.manager.review(tree['id']);self.assertIn('0o755',review['preview'])
        result=self.manager.integrate(review['review']);self.assertEqual((self.root/'a.py').stat().st_mode&0o777,0o755)
        rewind(self.parent,result['changeset']);self.assertEqual((self.root/'a.py').stat().st_mode&0o777,0o644)

    def test_rename_interruption_after_destination_keeps_recoverable_original(self):
        tree=self.tree();root=Path(tree['path']);(root/'a.py').rename(root/'renamed.py')
        review=self.manager.review(tree['id'],new_paths=['renamed.py'])
        with patch('codine.changesets.remove_guarded',side_effect=OSError('simulated removal failure')):
            with self.assertRaises(OSError):self.manager.integrate(review['review'])
        self.assertTrue((self.root/'a.py').exists());self.assertTrue((self.root/'renamed.py').exists())
        record=ChangesetStore(self.root).list()[0]
        rewind(self.parent,record['id'])
        self.assertTrue((self.root/'a.py').exists());self.assertFalse((self.root/'renamed.py').exists())

    def test_deleted_source_conflict_blocks_rename_destination(self):
        tree=self.tree();root=Path(tree['path']);(root/'a.py').rename(root/'renamed.py')
        review=self.manager.review(tree['id'],new_paths=['renamed.py'])
        (self.root/'a.py').write_text('new human data\n')
        with self.assertRaisesRegex(ValueError,'conflict'):self.manager.integrate(review['review'])
        self.assertFalse((self.root/'renamed.py').exists());self.assertEqual((self.root/'a.py').read_text(),'new human data\n')

    def test_snapshot_cleanup_never_follows_links_and_is_resumable(self):
        tree=self.tree();outside=self.home/'outside';outside.mkdir();(outside/'keep.txt').write_text('keep')
        (Path(tree['path'])/'escape').symlink_to(outside,target_is_directory=True)
        with patch('codine.tree_cleanup.remove_tree',side_effect=OSError('simulated failure')):
            with self.assertRaises(OSError):self.manager.cleanup(tree['id'])
        self.assertEqual(self.manager._load(tree['id'],require_tree=False)['status'],'cleanup_pending')
        self.assertEqual(self.manager.cleanup(tree['id'])['status'],'cleaned')
        self.assertEqual((outside/'keep.txt').read_text(),'keep')
        self.assertFalse(Path(tree['path']).exists())
        self.assertEqual(self.manager.cleanup(tree['id'])['status'],'cleaned')

    def test_second_tree_conflict_cannot_overwrite_first_integration(self):
        first=self.tree();second=self.tree()
        self.edit(first,'a.py','first=1\n');self.edit(second,'a.py','second=1\n')
        review_first=self.manager.review(first['id']);review_second=self.manager.review(second['id'])
        self.manager.integrate(review_first['review'])
        with self.assertRaisesRegex(ValueError,'conflict'):self.manager.integrate(review_second['review'])
        self.assertEqual((self.root/'a.py').read_text(),'first=1\n')
