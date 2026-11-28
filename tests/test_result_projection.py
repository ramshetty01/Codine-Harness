import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from codine.artifacts import ArtifactStore
from codine.config import Redactor
from codine.result_projection import project,parse
from codine.workspace import Workspace


class ProjectionTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name).resolve();self.ws=Workspace(self.root,lambda _:True,lambda _:None)
        self.store=ArtifactStore(self.root,self.ws)
        self.agent=SimpleNamespace(session=SimpleNamespace(messages=[],pending=None),redactor=Redactor(['private-test-secret']))

    def history(self,value,ident='a'):
        self.agent.session.messages.extend([
            {'role':'assistant','tool_calls':[{'id':ident,'function':{'name':'external','arguments':'{}'}}]},
            {'role':'tool','tool_call_id':ident,'content':json.dumps(value)}])

    def test_typed_pointer_pagination_redaction_and_no_file_read_authority(self):
        self.history({'rows':[{'id':i,'text':'private-test-secret','a/b':{'~key':False}} for i in range(10)]})
        result=project(self.agent,self.store,tool_call_id='a',pointer_path='/rows',fields=['/id','/text','/a~1b/~0key'],offset=2,limit=2)
        self.assertEqual(result['value'][0],{'/id':2,'/text':'[REDACTED]','/a~1b/~0key':False})
        self.assertEqual(result['next_offset'],4);self.assertTrue(result['truncated'])
        self.assertEqual(result['source']['message_index'],1)
        self.assertEqual(result['source']['source_tool'],'external')
        self.assertEqual(self.ws.read_versions,{})

    def test_ambiguous_ids_require_index_and_pending_results_available(self):
        self.history({'x':1});self.history({'x':2})
        with self.assertRaisesRegex(ValueError,'ambiguous'):project(self.agent,self.store,tool_call_id='a')
        self.assertEqual(project(self.agent,self.store,message_index=3)['value'],{'x':2})
        self.agent.session.pending={'reply':{'tool_calls':[{'id':'pending','function':{'name':'external'}}]},'results':{'pending':'{"value":true}'}}
        self.assertEqual(project(self.agent,self.store,tool_call_id='pending')['value'],{'value':True})

    def test_strict_json_and_nonexecutable_selectors(self):
        for text in ('{"x":1,"x":2}','{"x":NaN}','[Infinity]','[1e999]', '['*40+'0'+']'*40):
            with self.assertRaises(ValueError):parse(text)
        self.history({'url':'https://example.invalid/','x':[1,2]})
        for pointer in ('__import__("os")','/x/-1','/x/01','/bad~2pointer','/missing'):
            with self.assertRaises(ValueError):project(self.agent,self.store,tool_call_id='a',pointer_path=pointer)
        self.assertFalse(list(self.root.iterdir()))

    def test_artifact_integrity_excerpt_provenance_and_bounded_output(self):
        artifact=self.store.put('external',json.dumps({'rows':[{'x':i} for i in range(100)]}))
        self.history({'truncated':True,'artifact_id':artifact,'preview':'excerpt'})
        result=project(self.agent,self.store,tool_call_id='a')
        self.assertFalse(result['source']['source_complete'])
        self.assertEqual(result['source']['full_source_artifact_id'],artifact)
        full=project(self.agent,self.store,artifact_id=artifact,pointer_path='/rows',limit=2)
        self.assertTrue(full['source']['source_complete']);self.assertEqual(full['total_items'],100)
        path=self.root/'.codine/artifacts'/f'{artifact}.json';path.write_text('{}')
        with self.assertRaisesRegex(ValueError,'changed'):project(self.agent,self.store,artifact_id=artifact)
        self.history({'large':'x'*10000},ident='large')
        with self.assertRaisesRegex(ValueError,'8 KiB'):project(self.agent,self.store,tool_call_id='large')

    def test_explicit_nested_json_decode_and_artifact_lock_no_wait(self):
        self.history({'text':'[{"id":1},{"id":2}]'})
        self.assertEqual(project(self.agent,self.store,tool_call_id='a',pointer_path='/text',decode_json=True,fields=['/id'])['value'],[{'/id':1},{'/id':2}])
        artifact=self.store.put('external','{}')
        with self.store.directory():
            with self.assertRaisesRegex(ValueError,'busy'):project(self.agent,self.store,artifact_id=artifact)
