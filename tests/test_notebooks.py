import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
from codine.notebooks import Notebooks
from codine.workspace import Workspace
from codine.tools import build_registry


class NotebookTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.ws = Workspace(self.root, Mock(return_value=True), Mock(), track_changes=True)
        self.nb = Notebooks(self.ws)
        self.path = self.root / 'analysis.ipynb'
        self.document = {'nbformat': 4, 'nbformat_minor': 5, 'metadata': {'kernelspec': {'name': 'python3'}},
            'cells': [{'id': 'intro', 'cell_type': 'markdown', 'metadata': {'tags': ['keep']}, 'source': ['# Analysis\n', 'Read carefully']},
                      {'id': 'compute', 'cell_type': 'code', 'metadata': {'custom': True}, 'source': 'print(1)\n',
                       'execution_count': 5, 'outputs': [{'output_type': 'display_data', 'data': {'text/html': '<script>evil()</script>'}}]}]}
        self.path.write_text(json.dumps(self.document))

    def test_cells_are_structured_not_executed_and_no_generic_edit_grants(self):
        result = self.nb.read('analysis.ipynb', cell=1)
        self.assertEqual(result['source'], 'print(1)\n')
        self.assertEqual(result['output_count'], 1)
        self.assertNotIn('<script>', json.dumps(result))
        self.assertFalse(self.ws.read_versions)
        self.assertFalse(self.ws.read_ranges)
        self.assertFalse(self.ws.lifecycle_versions)

    def test_edit_preserves_unrelated_data_clears_stale_output_and_can_rewind(self):
        result = self.nb.read('analysis.ipynb', cell=1)
        before = self.path.read_bytes()
        change = self.nb.edit('analysis.ipynb', 1, 'print(2)\n', result['sha256'])
        self.assertFalse(change['executed'])
        edited = json.loads(self.path.read_text())
        self.assertEqual(edited['metadata'], self.document['metadata'])
        self.assertEqual(edited['cells'][0], self.document['cells'][0])
        self.assertEqual(edited['cells'][1]['metadata'], self.document['cells'][1]['metadata'])
        self.assertEqual(edited['cells'][1]['outputs'], [])
        self.assertIsNone(edited['cells'][1]['execution_count'])
        from codine.changesets import rewind
        rewind(self.ws, self.ws.change_group_id)
        self.assertEqual(self.path.read_bytes(), before)

    def test_incomplete_read_and_cross_session_grants_rejected(self):
        result = self.nb.read('analysis.ipynb', cell=0, max_chars=3)
        with self.assertRaisesRegex(ValueError, 'entire'):
            self.nb.edit('analysis.ipynb', 0, 'replacement', result['sha256'])
        self.nb.read('analysis.ipynb', cell=0, start_char=3, expected_sha256=result['sha256'])
        self.ws.change_session_id = 'different-session'
        with self.assertRaisesRegex(ValueError, 'entire'):
            self.nb.edit('analysis.ipynb', 0, 'replacement', result['sha256'])

    def test_paging_staleness_and_approval_race_preserve_external_edit(self):
        result = self.nb.read('analysis.ipynb', cell=1)
        self.path.write_text(json.dumps({**self.document, 'metadata': {'external': True}}))
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.nb.read('analysis.ipynb', expected_sha256=result['sha256'])
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.nb.edit('analysis.ipynb', 1, 'print(2)', result['sha256'])
        result = self.nb.read('analysis.ipynb', cell=1)
        def approval(_):
            self.path.write_text('external writer')
            return True
        self.ws.approve = approval
        with self.assertRaises(ValueError):
            self.nb.edit('analysis.ipynb', 1, 'print(2)', result['sha256'])
        self.assertEqual(self.path.read_text(), 'external writer')

    def test_decline_and_planning_preserve_file(self):
        result = self.nb.read('analysis.ipynb', cell=1)
        before = self.path.read_bytes()
        self.ws.approve = Mock(return_value=False)
        self.assertEqual(self.nb.edit('analysis.ipynb', 1, 'other', result['sha256'])['status'], 'declined')
        self.assertEqual(self.path.read_bytes(), before)
        self.ws.mode = 'plan'
        with self.assertRaises(ValueError): self.nb.edit('analysis.ipynb', 1, 'other', result['sha256'])
        registry = build_registry(self.ws, lambda: None)
        self.assertIn('read_notebook', registry.tools)
        self.assertFalse(registry.allowed(registry.tools['edit_notebook_cell']))

    def test_bad_format_limits_and_private_paths(self):
        for text in ('{"nbformat":4,"nbformat":3}', '{"nbformat":3,"cells":[]}',
                     json.dumps({**self.document, 'cells': self.document['cells'] * 501}),
                     '{"nbformat":4,"cells":[],"metadata":{"value":NaN}}'):
            self.path.write_text(text)
            with self.assertRaises(ValueError): self.nb.read('analysis.ipynb')
        self.path.write_text(json.dumps(self.document))
        (self.root/'linked.ipynb').symlink_to(self.path)
        with self.assertRaises(ValueError): self.nb.read('linked.ipynb')
        with self.assertRaises(ValueError): self.nb.read('../outside.ipynb')
        with self.assertRaises(ValueError): self.nb.read('.env')

    def test_empty_notebook_and_source_representations(self):
        self.path.write_text(json.dumps({**self.document, 'cells': []}))
        self.assertEqual(self.nb.read('analysis.ipynb')['total_cells'], 0)
        self.path.write_text(json.dumps(self.document))
        result = self.nb.read('analysis.ipynb', cell=0)
        self.nb.edit('analysis.ipynb', 0, '# Changed\nbody', result['sha256'])
        self.assertEqual(json.loads(self.path.read_text())['cells'][0]['source'], ['# Changed\n', 'body'])

    def test_basic_schema_depth_and_json_value_limits(self):
        cases=[]
        for key,value in (('nbformat_minor',True),('nbformat_minor',-1),('metadata',None)):
            data=copy.deepcopy(self.document);data[key]=value;cases.append(data)
        for key in ('nbformat_minor','metadata'):
            data=copy.deepcopy(self.document);del data[key];cases.append(data)
        for key in ('source','metadata','execution_count','outputs','id'):
            data=copy.deepcopy(self.document);del data['cells'][1][key];cases.append(data)
        for key,value in (('source','\ud800'),('outputs',{}),('execution_count',True),('execution_count',-1),('id','bad id')):
            data=copy.deepcopy(self.document);data['cells'][1][key]=value;cases.append(data)
        data=copy.deepcopy(self.document);data['cells'][1]['id']='intro';cases.append(data)
        nested={}
        for _ in range(65): nested={'nested':nested}
        data=copy.deepcopy(self.document);data['metadata']=nested;cases.append(data)
        data=copy.deepcopy(self.document);data['metadata']={'values':[0]*100001};cases.append(data)
        for data in cases:
            with self.subTest(keys=list(data)):
                self.path.write_text(json.dumps(data))
                before=self.path.read_bytes()
                with self.assertRaises(ValueError):self.nb.read('analysis.ipynb')
                self.assertEqual(self.path.read_bytes(),before)
        self.path.write_text(json.dumps(self.document).replace('"custom": true','"custom": 1e999'))
        with self.assertRaisesRegex(ValueError,'number'):self.nb.read('analysis.ipynb')
        legacy=copy.deepcopy(self.document);legacy['nbformat_minor']=4
        for cell in legacy['cells']:del cell['id']
        self.path.write_text(json.dumps(legacy))
        self.assertEqual(self.nb.read('analysis.ipynb')['total_cells'],2)

    def test_denied_read_and_write_policies_never_reach_approval_or_mutation(self):
        from unittest.mock import patch
        from codine.command_policy import CommandPolicy, FileRule
        def policy(operation):
            self.ws.command_policy=CommandPolicy(self.root/'policy.json',file_rules=(
                FileRule(id='deny',action='deny',operation=operation,project=str(self.ws.root),match='exact',path='analysis.ipynb'),))
        policy('read')
        with patch.object(self.ws,'_read',side_effect=AssertionError('denied disk read')):
            with self.assertRaisesRegex(ValueError,'policy'):self.nb.read('analysis.ipynb')
        policy('write')
        result=self.nb.read('analysis.ipynb',cell=1)
        before=self.path.read_bytes()
        with self.assertRaisesRegex(ValueError,'policy'):
            self.nb.edit('analysis.ipynb',1,'print(2)',result['sha256'])
        self.ws.approve.assert_not_called()
        self.assertEqual(self.path.read_bytes(),before)
        self.assertEqual(self.nb.coverage[self.nb.key('analysis.ipynb',1,result['sha256'])],[(0,9)])

    def test_fake_agent_reads_edits_receives_result_and_rewinds_group(self):
        from codine.agent import Agent
        from codine.config import Config, Redactor
        from codine.sessions import Session, SessionStore, validate_messages
        from codine.changesets import rewind
        requests=[]
        def call(name,args):
            return {'role':'assistant','content':None,'tool_calls':[{'id':'call'+str(len(requests)),
                'type':'function','function':{'name':name,'arguments':json.dumps(args)}}]}
        class FakeProvider:
            def complete(_,messages,schemas,model,emit):
                requests.append(copy.deepcopy(messages))
                if len(requests)==1:
                    return call('read_notebook',{'path':'analysis.ipynb','cell':1}),'tool_calls',None
                result=json.loads(messages[-1]['content'])
                if len(requests)==2:
                    self.assertEqual(result['source'],'print(1)\n')
                    return call('edit_notebook_cell',{'path':'analysis.ipynb','cell':1,'new_source':'print(3)\n','expected_sha256':result['sha256']}),'tool_calls',None
                self.assertEqual(result['status'],'updated')
                self.assertFalse(result['executed'])
                return {'role':'assistant','content':'Edited; not executed.'},'stop',None
        before=self.path.read_bytes()
        store=SessionStore(self.root);self.addCleanup(store.close)
        session=Session.create(self.root,'fake','system')
        self.ws.change_session_id=session.id
        registry=build_registry(self.ws,lambda:None)
        agent=Agent(Config(self.root),FakeProvider(),registry,store,session,Mock(),Redactor())
        self.assertEqual(agent.run_turn('Read and edit the code cell.'),'completed')
        self.assertEqual(len(requests),3)
        validate_messages(store.load(session.id).messages)
        edited=json.loads(self.path.read_text())
        self.assertEqual(edited['cells'][1]['source'],'print(3)\n')
        self.assertEqual(edited['cells'][1]['outputs'],[])
        self.assertEqual(edited['cells'][0],self.document['cells'][0])
        self.ws.approve.assert_called_once()
        rewind(self.ws,self.ws.change_group_id)
        self.assertEqual(self.path.read_bytes(),before)
    def test_switch_away_and_back_without_notebook_call_revokes_old_read(self):
        from codine.agent import Agent
        from codine.cli import command
        from codine.config import Config, Redactor
        from codine.sessions import Session, SessionStore
        store=SessionStore(self.root);self.addCleanup(store.close)
        session=Session.create(self.root,'fake','system')
        store.save(session)
        agent=Agent(Config(self.root),Mock(),Mock(),store,session,Mock(),Redactor())
        self.ws.change_session_id=session.id
        result=self.nb.read('analysis.ipynb',cell=1)
        self.assertTrue(self.nb.coverage)
        ui=Mock();ui.interactive=False
        command('/new',agent,self.ws,ui)
        self.assertNotEqual(agent.session.id,session.id)
        self.assertFalse(self.nb.coverage)
        command('/resume '+session.id,agent,self.ws,ui)
        self.assertEqual(agent.session.id,session.id)
        before=self.path.read_bytes()
        with self.assertRaisesRegex(ValueError,'entire'):
            self.nb.edit('analysis.ipynb',1,'print(2)',result['sha256'])
        self.assertEqual(self.path.read_bytes(),before)
        self.nb.read('analysis.ipynb',cell=1)
        self.assertEqual(self.nb.edit('analysis.ipynb',1,'print(2)',result['sha256'])['status'],'updated')
