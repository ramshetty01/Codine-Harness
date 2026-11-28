import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from codine.extension_commands import command, discover
from codine.extension_manifest import read_bytes
from codine.workspace import Workspace


class ExtensionCommandTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name).resolve()
        self.ws=Workspace(self.root,Mock(return_value=True),Mock())
        self.ui=SimpleNamespace(write=Mock())
        self.manager=Mock()
        self.agent=SimpleNamespace(extension_manager=self.manager,_in_turn=False)
        self.manifest={'version':1,'name':'demo','entrypoint':'main.py','files':['main.py'],
                       'capabilities':['tools','commands','ui']}
        (self.root/'package').mkdir()
        self.path=self.root/'package/custom-name.json'
        self.path.write_text(json.dumps(self.manifest))
        (self.root/'package/main.py').write_text('raise RuntimeError("MUST NOT IMPORT")')

    def test_discovery_reads_only_manifest_not_source_and_requires_trust(self):
        def checked_read(root,name,limit):
            self.assertTrue(name.endswith('.json'))
            return read_bytes(root,name,limit)
        with patch('codine.extension_commands.read_bytes',side_effect=checked_read):
            rows=discover(self.ws)
        self.assertEqual(rows['extensions'][0]['manifest_path'],'package/custom-name.json')
        self.assertTrue(rows['extensions'][0]['trust_required'])
        self.assertNotIn('digest',rows['extensions'][0])
        self.assertNotIn('MUST NOT IMPORT',json.dumps(rows))
        self.ws.approve.assert_not_called()
        self.manager.assert_not_called()
        (self.root/'package/main.py').unlink()
        self.assertEqual(len(discover(self.ws)['extensions']),1)

    def test_ignored_private_links_and_malformed_metadata_not_disclosed(self):
        (self.root/'.gitignore').write_text('ignored/\n')
        (self.root/'ignored').mkdir();(self.root/'ignored/extension.json').write_text(json.dumps(self.manifest))
        (self.root/'.env.json').write_text(json.dumps(self.manifest))
        (self.root/'broken.json').write_text('{oops')
        (self.root/'ordinary.json').write_text('{"some":"data"}')
        (self.root/'linked.json').symlink_to(self.path)
        result=discover(self.ws)
        self.assertEqual([row['manifest_path'] for row in result['extensions']],['package/custom-name.json'])
        for hidden in ('ignored/extension.json','.env.json','linked.json','broken.json'):
            self.assertNotIn(hidden,json.dumps(result))

    def test_ask_and_deny_never_prompt_or_disclose(self):
        from codine.command_policy import CommandPolicy, FileRule
        for action in ('ask','deny'):
            self.ws.command_policy=CommandPolicy(self.root/'policy.json',file_rules=(
                FileRule(id='guard',action=action,operation='read',project=str(self.root),match='exact',path='package/custom-name.json'),))
            result=discover(self.ws)
            self.assertEqual(result['extensions'],[])
            self.assertNotIn('custom-name',json.dumps(result))
        self.ws.approve.assert_not_called()
        (self.root/'.gitignore').write_text('ignored/\n')
        self.ws.command_policy=CommandPolicy(self.root/'policy.json',file_rules=(
            FileRule(id='guard',action='ask',operation='read',project=str(self.root),match='exact',path='.gitignore'),))
        result=discover(self.ws)
        self.assertTrue(result['truncated'])
        self.assertEqual(result['extensions'],[])
        self.ws.approve.assert_not_called()

    def test_discovery_bounds_and_narrow_scope(self):
        for index in range(40):
            (self.root/f'extension-{index}.json').write_text(json.dumps(self.manifest))
        result=discover(self.ws)
        self.assertEqual(len(result['extensions']),32)
        self.assertTrue(result['truncated'])
        self.assertEqual(len(discover(self.ws,'package')['extensions']),1)
        self.assertEqual(discover(self.ws,'../')['extensions'],[])

    def test_command_dispatch_and_opaque_argument_no_interpretation(self):
        for action,arg in [('list',''),('load','package/custom-name.json'),('unload','demo'),('reload','demo'),('panel','demo:status')]:
            method=getattr(self.manager,action);method.return_value={'status':action}
            result=command('extensions',(action+' '+arg).strip(),self.agent,self.ws,self.ui)
            method.assert_called_once_with(*([arg] if arg else []))
            self.assertEqual(result,{'status':action})
        self.manager.invoke_command.return_value={'answer':'okay'}
        command('ext','demo:hello $(touch nope);  literal',self.agent,self.ws,self.ui)
        self.manager.invoke_command.assert_called_once_with('demo:hello','$(touch nope);  literal')
        self.assertFalse((self.root/'nope').exists())

    def test_discovery_without_runtime_and_invalid_or_busy_actions(self):
        bare=SimpleNamespace(_in_turn=False)
        self.assertEqual(len(command('extensions','discover',bare,self.ws,self.ui)['extensions']),1)
        with self.assertRaisesRegex(ValueError,'unavailable'):command('extensions','load package/custom-name.json',bare,self.ws,self.ui)
        for kind,arg in [('extensions','list extra'),('extensions','reload'),('extensions','automatic'),('extensions','panel invalid'),('ext','no_namespace')]:
            with self.assertRaises(ValueError):command(kind,arg,self.agent,self.ws,self.ui)
        self.agent._in_turn=True
        with self.assertRaisesRegex(ValueError,'idle'):command('extensions','reload demo',self.agent,self.ws,self.ui)
        self.manager.reload.assert_not_called()

    def test_runtime_trust_failure_is_not_swallowed_or_retried(self):
        self.manager.reload.side_effect=ValueError('Extension changed; review new exact snapshot')
        with self.assertRaisesRegex(ValueError,'changed'):
            command('extensions','reload demo',self.agent,self.ws,self.ui)
        self.manager.reload.assert_called_once_with('demo')
        self.manager.unload.assert_not_called()
        self.manager.load.assert_not_called()
        self.ui.write.assert_not_called()

    def test_cli_routes_explicit_commands_without_model_turn(self):
        from codine.cli import command as cli_command
        self.agent.session=SimpleNamespace()
        self.manager.list.return_value=[]
        cli_command('/extensions list',self.agent,self.ws,self.ui)
        self.manager.list.assert_called_once_with()
        self.manager.invoke_command.return_value='Hello'
        cli_command('/ext demo:hello text',self.agent,self.ws,self.ui)
        self.manager.invoke_command.assert_called_once_with('demo:hello','text')
