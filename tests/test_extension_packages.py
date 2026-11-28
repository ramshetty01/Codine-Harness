import importlib.metadata
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from codine.extension_packages import Packages, parse, compatibility
from codine.extension_commands import discover
from codine.extension_trust import ExtensionTrustStore
from codine.workspace import Workspace


class PackageTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name).resolve()
        self.source=self.root/'source';self.source.mkdir()
        self.ws=Workspace(self.root,Mock(return_value=True),Mock())
        self.manager=SimpleNamespace(list=lambda:[])
        self.packages=Packages(self.ws,self.manager)
        self.package={'schema_version':1,'name':'demo','version':'1.2.0','extension':'manifest.json',
                      'python':{'min':'3.11','max_exclusive':'4.0'},'api':1,'dependencies':{}}
        self.manifest={'version':1,'name':'demo','entrypoint':'main.py','files':['main.py','docs/guide.md'],
                       'capabilities':['commands']}
        self.write_package()
        (self.source/'manifest.json').write_text(json.dumps(self.manifest))
        (self.source/'main.py').write_text('raise RuntimeError("NEVER IMPORT WHILE INSTALLING")\n')
        (self.source/'docs').mkdir();(self.source/'docs/guide.md').write_text('documentation')
        self.emit=Mock();self.approve=Mock(return_value=True)

    def write_package(self): (self.source/'package.json').write_text(json.dumps(self.package))
    def review(self): return self.packages.review('source')['digest']
    def install(self): return self.packages.install(self.review(),self.approve,self.emit)

    def test_review_install_discovery_no_execution_or_trust_then_retained_removal(self):
        digest=self.review()
        self.assertFalse((self.root/'.codine-extensions').exists())
        result=self.packages.install(digest,self.approve,self.emit)
        self.assertEqual(result['status'],'installed');self.assertFalse(result['trust_granted'])
        self.assertTrue(any('+++ b/.codine-extensions/demo/main.py' in call.args[0] for call in self.emit.call_args_list))
        self.assertIn('.codine-extensions/demo/manifest.json',[row['manifest_path'] for row in discover(self.ws)['extensions']])
        trust=ExtensionTrustStore(self.root/'private-trust')
        self.assertFalse(trust.trusted(trust.review(self.ws,result['manifest_path'])))
        output=self.packages.remove('demo',self.approve,self.emit)
        self.assertEqual(output['status'],'removed')
        self.assertFalse((self.root/result['destination']).exists())
        self.assertEqual((self.root/output['backup']/'main.py').read_bytes(),(self.source/'main.py').read_bytes())
        self.assertEqual(self.packages.list()[0]['status'],'removed')
        with self.assertRaisesRegex(ValueError,'receipt'):self.install()

    def test_decline_or_changed_source_or_destination_does_not_install(self):
        digest=self.review()
        self.assertEqual(self.packages.install(digest,lambda _:False,self.emit)['status'],'declined')
        self.assertFalse((self.root/'.codine-extensions/demo').exists())
        def change(_):
            (self.source/'main.py').write_text('changed');return True
        with self.assertRaisesRegex(ValueError,'changed'):self.packages.install(digest,change,self.emit)
        digest=self.review()
        def occupy(_):
            (self.root/'.codine-extensions/demo').mkdir(parents=True);return True
        with self.assertRaisesRegex(ValueError,'destination'):self.packages.install(digest,occupy,self.emit)
        self.assertTrue((self.root/'.codine-extensions/demo').is_dir())

    def test_compatibility_checks_without_dependency_import_or_install(self):
        self.package['dependencies']={'some-dist':'2.1'}
        self.write_package();self.packages.version_lookup=lambda name:'2.0'
        result=self.packages.review('source')
        self.assertFalse(result['compatibility']['compatible'])
        with self.assertRaisesRegex(ValueError,'compatibility'):
            self.packages.install(result['digest'],self.approve,self.emit)
        self.approve.assert_not_called()
        self.assertFalse(compatibility(self.package,lambda _: '2.1',python_version=(3,10))['compatible'])
        self.assertTrue(compatibility(self.package,lambda _: '2.1',python_version=(3,11))['compatible'])
        self.package['api']=2
        self.assertFalse(compatibility(self.package,lambda _:'2.1')['compatible'])

    def test_removed_refuses_external_changes_untracked_files_and_loaded_extension(self):
        self.install();target=self.root/'.codine-extensions/demo/main.py'
        before=target.read_bytes();target.write_text('external edit')
        with self.assertRaisesRegex(ValueError,'changed'):self.packages.remove('demo',self.approve,self.emit)
        target.write_bytes(before)
        extra=target.parent/'untracked.txt';extra.write_text('keep')
        with self.assertRaisesRegex(ValueError,'untracked'):self.packages.remove('demo',self.approve,self.emit)
        extra.unlink()
        self.manager.list=lambda:[{'name':'demo','manifest_path':'.codine-extensions/demo/manifest.json'}]
        with self.assertRaisesRegex(ValueError,'Unload'):self.packages.remove('demo',self.approve,self.emit)
        self.assertTrue(target.exists())

    def test_source_links_credentials_and_shared_destination_parent_refused(self):
        original=self.source/'main.py';alias=self.source/'alias.py';os.link(original,alias)
        with self.assertRaises(ValueError):self.review()
        alias.unlink();original.unlink();original.symlink_to(self.source/'docs/guide.md')
        with self.assertRaises((OSError,ValueError)):self.review()
        original.unlink();original.write_text('safe')
        self.manifest['files'].append('.env');(self.source/'.env').write_text('fake-secret')
        (self.source/'manifest.json').write_text(json.dumps(self.manifest))
        with self.assertRaises(ValueError):self.review()
        self.manifest['files'].pop();(self.source/'manifest.json').write_text(json.dumps(self.manifest))
        dest=self.root/'.codine-extensions';dest.mkdir();dest.chmod(0o777)
        with self.assertRaisesRegex(ValueError,'owned'):self.install()

    def test_pending_receipt_preserves_unknown_install_outcome(self):
        digest=self.review();real=self.packages.save;calls=[]
        def fail_final(fd,rows):
            calls.append(1)
            if len(calls)==2:raise OSError('simulated state failure')
            return real(fd,rows)
        with patch.object(self.packages,'save',side_effect=fail_final):
            with self.assertRaises(OSError):self.packages.install(digest,self.approve,self.emit)
        self.assertEqual(self.packages.list()[0]['status'],'pending_install')
        self.assertTrue((self.root/'.codine-extensions/demo/main.py').exists())
        with self.assertRaisesRegex(ValueError,'receipt'):self.packages.install(digest,self.approve,self.emit)
        with self.assertRaisesRegex(ValueError,'receipt'):self.packages.remove('demo',self.approve,self.emit)

    def test_removal_edit_during_approval_refused_and_state_protected(self):
        self.install();target=self.root/'.codine-extensions/demo/main.py'
        def edit(_):target.write_text('keep this change');return True
        with self.assertRaisesRegex(ValueError,'changed'):self.packages.remove('demo',edit,self.emit)
        self.assertEqual(target.read_text(),'keep this change')
        for path in ('.codine/packages/receipts.json','.codine/packages/lock'):
            for operation in ('read','write'):
                with self.assertRaises(ValueError):self.ws.resolve(path,operation=operation)

    def test_invalid_schema_versions_and_metadata_paths(self):
        for field,value in [('version','latest'),('api',True),('extension','../manifest.json'),('name','../escape')]:
            data=dict(self.package);data[field]=value
            with self.assertRaises(ValueError):parse(json.dumps(data).encode())
        self.ws.mode='plan'
        with self.assertRaisesRegex(ValueError,'plan'):self.install()

    def test_atomic_publication_never_overwrites_late_empty_destination(self):
        from codine.rename_noreplace import rename_noreplace
        digest=self.review()
        def occupy_then_publish(source,destination,**kwargs):
            os.mkdir(destination,dir_fd=kwargs['dst_dir_fd'])
            return rename_noreplace(source,destination,**kwargs)
        with patch('codine.extension_packages.rename_noreplace',side_effect=occupy_then_publish):
            with self.assertRaises(FileExistsError):self.packages.install(digest,self.approve,self.emit)
        self.assertEqual(list((self.root/'.codine-extensions/demo').iterdir()),[])
        receipt=self.packages.list()[0]
        self.assertEqual(receipt['status'],'pending_install')
        self.assertTrue((self.root/'.codine/packages'/receipt['staging']/'main.py').exists())

    def test_file_replacement_with_identical_bytes_requires_manual_review(self):
        self.install();target=self.root/'.codine-extensions/demo/main.py'
        replacement=target.with_name('replacement.py');replacement.write_bytes(target.read_bytes());replacement.replace(target)
        with self.assertRaisesRegex(ValueError,'identity'):self.packages.remove('demo',self.approve,self.emit)

    def test_no_replace_helper_fails_closed_without_native_function(self):
        from codine.rename_noreplace import rename_noreplace
        with patch('codine.rename_noreplace.ctypes.CDLL',return_value=SimpleNamespace()):
            with self.assertRaisesRegex(OSError,'lacks'):
                rename_noreplace('source','destination',src_dir_fd=1,dst_dir_fd=1)

    def test_write_deny_policy_prevents_install_and_remove(self):
        from codine.command_policy import CommandPolicy,FileRule
        digest=self.review()
        self.ws.command_policy=CommandPolicy(self.root/'policy.json',file_rules=(
            FileRule(id='deny',action='deny',operation='write',project=str(self.root),match='exact',path='.codine-extensions/demo/main.py'),))
        with self.assertRaisesRegex(ValueError,'policy'):self.packages.install(digest,self.approve,self.emit)
        self.approve.assert_not_called()
        self.ws.command_policy=None;self.install()
        self.ws.command_policy=CommandPolicy(self.root/'policy.json',file_rules=(
            FileRule(id='deny',action='deny',operation='write',project=str(self.root),match='exact',path='.codine-extensions/demo/main.py'),))
        with self.assertRaisesRegex(ValueError,'policy'):self.packages.remove('demo',self.approve,self.emit)

    def test_installed_package_needs_separate_trust_before_real_worker_load(self):
        from codine.extension_manager import ExtensionManager
        from codine.config import Redactor
        from codine.registry import ToolRegistry
        (self.source/'main.py').write_text('def register(api):\n    api.command("hello", "Hello", lambda text: {"text": text})\n')
        trust=ExtensionTrustStore(self.root/'user-trust')
        runtime=ExtensionManager(self.ws,ToolRegistry(),trust,Redactor())
        self.addCleanup(runtime.close);self.packages.manager=runtime
        result=self.install()
        with self.assertRaises(ValueError):runtime.load(result['manifest_path'])
        snapshot=trust.review(self.ws,result['manifest_path'])
        trust.allow(self.ws,snapshot['digest'],lambda _:True)
        runtime.load(result['manifest_path'])
        self.assertEqual(runtime.invoke_command('demo:hello','hello'),{'text':'hello'})
        with self.assertRaisesRegex(ValueError,'Unload'):self.packages.remove('demo',self.approve,self.emit)
        runtime.unload('demo')
        self.assertEqual(self.packages.remove('demo',self.approve,self.emit)['status'],'removed')

    def test_source_dependency_change_after_approval_requires_new_review(self):
        self.package['dependencies']={'some-dist':'1.0'};self.write_package()
        self.packages.version_lookup=lambda _:'1.0';digest=self.review()
        def change(_):self.packages.version_lookup=lambda _:'2.0';return True
        with self.assertRaisesRegex(ValueError,'compatibility changed'):
            self.packages.install(digest,change,self.emit)
        self.assertFalse((self.root/'.codine-extensions/demo').exists())

    def test_removal_race_retains_bytes_in_protected_recovery_backup(self):
        from codine.rename_noreplace import rename_noreplace
        self.install()
        def change_then_move(source,destination,**kwargs):
            (self.root/'.codine-extensions/demo/main.py').write_text('racing change, keep me')
            return rename_noreplace(source,destination,**kwargs)
        with patch('codine.extension_packages.rename_noreplace',side_effect=change_then_move):
            with self.assertRaisesRegex(ValueError,'retained'):self.packages.remove('demo',self.approve,self.emit)
        row=self.packages.list()[0]
        self.assertEqual(row['status'],'removal_needs_review')
        self.assertEqual((self.root/'.codine/packages'/row['backup']/'main.py').read_text(),'racing change, keep me')

    def test_cli_routes_review_without_model_turn_or_approval(self):
        from codine.cli import command
        ui=SimpleNamespace(write=Mock(),approve=Mock(return_value=True))
        agent=SimpleNamespace(session=SimpleNamespace(),_in_turn=False,extension_packages=self.packages)
        command('/packages review source',agent,self.ws,ui)
        ui.approve.assert_not_called()
        self.assertIn('trust_granted',ui.write.call_args.args[0])
