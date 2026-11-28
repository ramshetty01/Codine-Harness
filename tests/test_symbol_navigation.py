import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
from codine.config import APP_ROOT
from codine.symbol_navigation import navigate
from codine.workspace import Workspace


class SymbolNavigationTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name).resolve()
        self.ws=Workspace(self.root,lambda _:False,lambda _:None)

    def file(self,name,text):
        path=self.root/name; path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(text); return path

    def test_multimodule_task_definitions_alias_and_shadow_candidates(self):
        shutil.copytree(APP_ROOT/'tests/fixtures/navigation',self.root,dirs_exist_ok=True)
        result=navigate(self.ws,symbol='normalize')
        definitions={row['qualified_name']:row for row in result['matches']}
        self.assertEqual(definitions['package.calculation.normalize']['line'],2)
        self.assertEqual(definitions['package.calculation.Formatter.normalize']['kind'],'method')
        self.assertEqual(definitions['package.service.shadowed.normalize']['kind'],'parameter')
        self.assertTrue(all(len(row['sha256'])==64 for row in definitions.values()))
        references=navigate(self.ws,'references','package.calculation.normalize')
        rows=references['matches']
        self.assertEqual({row['kind'] for row in rows},{'import_reference','attribute_reference','name_reference'})
        self.assertTrue(any(row['scope']=='package.service.shadowed' for row in rows))
        self.assertIn('not resolved',references['limitations'])
        self.assertFalse(any(row['name']=='clean' for row in rows))
        alias=navigate(self.ws,'references','clean')
        self.assertEqual(alias['matches'][0]['scope'],'package.service.build_message')
        self.assertEqual(self.ws.read_versions,{})
        with self.assertRaisesRegex(ValueError,'Read'):
            self.ws.edit_file('package/calculation.py','return value.strip().lower()','return value')

    def test_syntax_diagnostic_locates_broken_function_without_execution(self):
        self.file('broken.py','def valid():\n    return 1\n\ndef broken(:\n    return 2\n')
        self.file('side_effect.py',f"from pathlib import Path\nPath({str(self.root / 'executed')!r}).write_text('BAD')\n")
        result=navigate(self.ws,'diagnostics')
        self.assertEqual(result['diagnostics'][0]['path'],'broken.py')
        self.assertEqual(result['diagnostics'][0]['line'],4)
        self.assertEqual(result['diagnostics'][0]['kind'],'syntax_error')
        self.assertFalse(result['truncated'])
        self.assertFalse((self.root/'executed').exists())
        partial=navigate(self.ws,'definitions')
        self.assertIn('syntax_errors',partial['reasons'])
        self.assertTrue(partial['truncated'])

    def test_unicode_columns_and_explicit_other_language_fallback(self):
        self.file('unicode.py','π = object()\nπ.normalize()\n')
        self.file('consumer.js','// normalize comment\nfunction normalize(x) { return x; }\n')
        result=navigate(self.ws,'references','normalize')
        self.assertEqual(result['matches'][0]['column'],3)
        self.assertEqual(result['unsupported_files'],1)
        fallback=navigate(self.ws,'definitions','normalize',glob='*.js',language='text')
        self.assertEqual(fallback['analysis'],'literal_text_fallback')
        self.assertEqual(len(fallback['matches']),2)
        with self.assertRaisesRegex(ValueError,'cannot produce'):
            navigate(self.ws,'diagnostics',language='text')

    def test_ignore_secret_and_links_do_not_enter_parser(self):
        self.file('.gitignore','ignored.py\n')
        self.file('ignored.py','def ignored(): pass\n')
        secret=self.file('.env','credential_canary')
        os.link(secret,self.root/'alias.py')
        (self.root/'linked.py').symlink_to(secret)
        self.file('safe.py','def visible(): pass\n')
        result=navigate(self.ws)
        self.assertEqual([row['name'] for row in result['matches']],['visible'])
        self.assertNotIn('credential_canary',json.dumps(result))
        self.assertEqual(navigate(self.ws,symbol='ignored',respect_ignore=False)['matches'][0]['name'],'ignored')

    def test_read_ask_decline_and_deny_do_not_grant_or_disclose(self):
        from codine.command_policy import CommandPolicy, FileRule
        self.file('asked.py','def asked_private(): pass\n')
        self.file('denied.py','def denied_private(): pass\n')
        rules=tuple(FileRule(id=action,action=action,operation='read',project=str(self.root),match='exact',path=name)
                    for action,name in [('ask','asked.py'),('deny','denied.py')])
        self.ws.command_policy=CommandPolicy(self.root/'policy.json',file_rules=rules)
        self.ws.policy_interactive=True
        self.ws.approve=Mock(return_value=False)
        with patch('codine.symbol_navigation.subprocess.run') as parser:
            result=navigate(self.ws)
            parser.assert_not_called()
        self.ws.approve.assert_called_once()
        self.assertEqual(result['matches'],[])
        self.assertEqual(result['skipped_files'],1)
        self.assertNotIn('private',json.dumps(result))
        self.assertEqual(self.ws.read_ranges,{})
        self.assertEqual(self.ws.lifecycle_versions,{})

    def test_limits_and_real_worker_timeout_keep_prompt_available(self):
        self.file('a.py','x=1\n'*100)
        with patch('codine.symbol_navigation.MAX_NODES',10):
            result=navigate(self.ws)
        self.assertIn('parser_limit',result['reasons'])
        self.assertEqual(result['matches'],[])
        started=time.monotonic()
        with patch('codine.symbol_navigation.AST_WORKER','import time; time.sleep(10)'),patch('codine.symbol_navigation.PARSE_SECONDS',.05):
            result=navigate(self.ws)
        self.assertIn('parser_timeout',result['reasons'])
        self.assertEqual(result['matches'],[])
        self.assertLess(time.monotonic()-started,2)
        self.assertTrue(navigate(self.ws,symbol='x',limit=1)['matches'])

    def test_result_file_source_and_byte_limits_report_partial_coverage(self):
        self.file('a.py','\n'.join('def name'+str(i)+'(): pass' for i in range(20)))
        self.file('b.py','def other(): pass\n')
        result=navigate(self.ws,limit=2)
        self.assertEqual(len(result['matches']),2)
        self.assertIn('result_limit',result['reasons'])
        with patch('codine.symbol_navigation.MAX_FILES',1):
            self.assertIn('file_limit',navigate(self.ws)['reasons'])
        with patch('codine.symbol_navigation.MAX_FILE_BYTES',10):
            result=navigate(self.ws)
            self.assertIn('source_size_limit',result['reasons'])
        with patch('codine.symbol_navigation.MAX_TOTAL_BYTES',10):
            self.assertIn('byte_limit',navigate(self.ws)['reasons'])

    def test_registered_read_only_tool_and_artifact_offload(self):
        from codine.tools import build_registry
        from codine.agent import Agent
        from codine.config import Config, Redactor
        from codine.sessions import Session, SessionStore
        from codine.artifacts import ArtifactStore
        self.file('many.py','\n'.join('def long_function_name_'+str(i)+'(): pass' for i in range(100)))
        self.ws.mode='plan'
        registry=build_registry(self.ws,lambda:None)
        call={'id':'symbols','type':'function','function':{'name':'navigate_symbols','arguments':'{"limit":100}'}}
        session=Session.create(self.root,'offline','system')
        store=SessionStore(self.root); self.addCleanup(store.close)
        provider=Mock(); provider.complete.side_effect=[({'role':'assistant','content':None,'tool_calls':[call]},'tool_calls',None),({'role':'assistant','content':'done'},'stop',None)]
        agent=Agent(Config(self.root),provider,registry,store,session,lambda *args:None,Redactor())
        agent.artifact_store=ArtifactStore(self.root,self.ws)
        agent.run_turn('find definitions')
        result=json.loads(next(row['content'] for row in session.messages if row['role']=='tool'))
        self.assertIn('artifact_id',result)
        self.assertEqual(self.ws.read_versions,{})

    def test_large_unicode_labels_hit_worker_output_cap_without_partial_claim(self):
        lines=[]
        for depth in range(6):
            lines.append('    '*depth+'class '+('界'*50)+str(depth)+':')
        for index in range(200):
            lines.append('    '*6+'def '+('名'*100)+str(index)+'(self): pass')
        self.file('labels.py','\n'.join(lines))
        result=navigate(self.ws,limit=200)
        self.assertIn('output_limit',result['reasons'])
        self.assertTrue(result['truncated'])
        self.assertEqual(result['matches'],[])
