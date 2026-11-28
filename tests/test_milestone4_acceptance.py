"""Offline M4 acceptance: real terminal, fake model, real file journals/recovery."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

from codine.sessions import validate_messages
from tests.test_terminal_integration import PTY


class Milestone4AcceptanceTests(unittest.TestCase):
    def test_inspect_switch_resume_and_rewind_related_edit_without_replay(self):
        driver = r'''
import json
from pathlib import Path
from codine import cli
class Fake:
    def __init__(self, config): self.root=config.project; self.calls=0
    def close(self): pass
    def complete(self, messages, schemas, model, emit):
        self.calls += 1
        (self.root / 'model-call-count.txt').write_text(str(self.calls))
        if self.calls == 1:
            calls = [{'id':'read-'+name, 'type':'function', 'function':{'name':'read_file', 'arguments':json.dumps({'path':name+'.py'})}} for name in ('alpha','beta')]
            return {'role':'assistant','content':None,'tool_calls':calls}, 'tool_calls', None
        if self.calls == 2:
            results = {message['tool_call_id']:json.loads(message['content']) for message in messages if message['role']=='tool'}
            changes = [{'path':name+'.py', 'content':name+' = 2\n', 'expected_sha256':results['read-'+name]['sha256']} for name in ('alpha','beta')]
            return {'role':'assistant','content':None,'tool_calls':[{'id':'grouped-edit','type':'function','function':{'name':'apply_changeset','arguments':json.dumps({'changes':changes})}}]}, 'tool_calls', None
        assert self.calls == 3, 'Session inspection/resume must not replay a model request'
        assert json.loads(messages[-1]['content'])['status'] == 'applied'
        emit('text','M4_EDIT_COMPLETE')
        return {'role':'assistant','content':'M4_EDIT_COMPLETE'}, 'stop', None
cli.NvidiaProvider = Fake
raise SystemExit(cli.main(['--provider','nvidia']))
'''
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            originals = {'alpha.py': b'alpha = 1\n', 'beta.py': b'beta = 1\r\n'}
            for name, content in originals.items():
                (root / name).write_bytes(content)
            terminal = PTY([sys.executable, '-c', driver], root)
            def approval(label, start=0):
                terminal.until(label, start=start)
                position = terminal.output.index(label, start)
                terminal.until(b'Approve? Type yes:', start=position)
            try:
                terminal.until('›'.encode())
                session_file = next((root / '.codine/sessions').glob('*.json'))
                original_session_id = session_file.stem
                terminal.send(b'Update both files as one related change.\r')
                approval(b'Apply grouped edit to alpha.py?')
                terminal.until(b'--- a/beta.py')
                for name, content in originals.items():
                    self.assertEqual((root / name).read_bytes(), content)
                self.assertIn(b'--- a/alpha.py', terminal.output)
                self.assertIn(b'--- a/beta.py', terminal.output)
                offset = len(terminal.output)
                terminal.send(b'yes\r')
                approval(b'Apply grouped edit to beta.py?', offset)
                # Both approvals must be collected before either file changes.
                for name, content in originals.items():
                    self.assertEqual((root / name).read_bytes(), content)
                terminal.send(b'yes\r')
                terminal.until(b'M4_EDIT_COMPLETE')
                offset = len(terminal.output)
                terminal.send(b'/tools\r')
                terminal.until(b'Tool history', start=offset)
                self.assertEqual((root / 'model-call-count.txt').read_text(), '3')
                snapshot = json.loads(session_file.read_text())
                validate_messages(snapshot['messages'])
                self.assertIsNone(snapshot['pending'])
                group = next('G' + str(index + 1) for index, message in enumerate(snapshot['messages'])
                             if any(call['function']['name'] == 'apply_changeset' for call in message.get('tool_calls', [])))
                changeset_file = next((root / '.codine/changesets').glob('*.json'))
                changeset = json.loads(changeset_file.read_text())
                ident = changeset['id']
                self.assertEqual(set(changeset['entries']), set(originals))
                for name in originals:
                    self.assertEqual((root / name).read_bytes(), (Path(name).stem + ' = 2\n').encode())

                offset = len(terminal.output)
                terminal.send(('/tools expand ' + group + '\r').encode())
                terminal.until(b'recorded result', start=offset)
                offset = len(terminal.output)
                terminal.send(('/details ' + group + '.1\r').encode())
                terminal.until(b'Historical output only', start=offset)
                self.assertIn(b'"status": "applied"', terminal.output[offset:])
                offset = len(terminal.output)
                terminal.send(('/changeset ' + ident + '\r').encode())
                terminal.until(('Changeset ' + ident).encode(), start=offset)
                terminal.until(b'--- a/beta.py', start=offset)

                offset = len(terminal.output)
                terminal.send(b'/new\r')
                terminal.until(b'Active session:', start=offset)
                self.assertEqual(len(list((root / '.codine/sessions').glob('*.json'))), 2)
                offset = len(terminal.output)
                terminal.send(b'/tools\r')
                terminal.until(b'No recorded tool groups', start=offset)
                offset = len(terminal.output)
                terminal.send(('/resume ' + original_session_id + '\r').encode())
                terminal.until(('Active session: ' + original_session_id).encode(), start=offset)
                resumed = json.loads(session_file.read_text())
                self.assertEqual(resumed['messages'], snapshot['messages'])
                self.assertEqual((root / 'model-call-count.txt').read_text(), '3')
                offset = len(terminal.output)
                terminal.send(('/details ' + group + '.1\r').encode())
                terminal.until(b'Historical output only', start=offset)
                self.assertIn(b'"status": "applied"', terminal.output[offset:])

                offset = len(terminal.output)
                terminal.send(('/rewind ' + ident + '\r').encode())
                approval(b'Rewind alpha.py as part', offset)
                terminal.until(b'--- a/beta.py', start=offset)
                self.assertIn(b'--- a/alpha.py', terminal.output[offset:])
                self.assertIn(b'--- a/beta.py', terminal.output[offset:])
                offset = len(terminal.output)
                terminal.send(b'yes\r')
                approval(b'Rewind beta.py as part', offset)
                for name in originals:
                    self.assertEqual((root / name).read_bytes(), (Path(name).stem + ' = 2\n').encode())
                terminal.send(b'yes\r')
                terminal.until(b'"status": "rewound"', start=offset)
                for name, content in originals.items():
                    self.assertEqual((root / name).read_bytes(), content)
                self.assertEqual(json.loads(changeset_file.read_text())['status'], 'rewound')
                terminal.send(b'exit\r')
                terminal.until(b'Goodbye!')
                self.assertEqual(terminal.process.wait(timeout=5), 0)
                final = json.loads(session_file.read_text())
                validate_messages(final['messages'])
                self.assertIsNone(final['pending'])
                self.assertEqual((root / 'model-call-count.txt').read_text(), '3')
                self.assertEqual(sum(call['function']['name'] == 'apply_changeset'
                                     for message in final['messages'] for call in message.get('tool_calls', [])), 1)
            finally:
                terminal.close()
