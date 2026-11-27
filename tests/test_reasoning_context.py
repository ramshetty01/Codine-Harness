import copy
from pathlib import Path
import unittest
from codine.context import project_context, request_message
from codine.sessions import Session, validate_messages

class ReasoningContextTests(unittest.TestCase):
    def test_exact_duplicate_is_omitted_without_mutating_archive(self):
        s=Session.create(Path.cwd(),'model','system',provider='openrouter')
        details=[{'type':'reasoning.text','text':'r'*10000,'format':'unknown','index':0}]
        s.messages += [{'role':'user','content':'task'}, {'role':'assistant','content':'answer','reasoning':'r'*10000,'reasoning_details':details}]
        before=copy.deepcopy(s.messages)
        projected,_=project_context(s,[],12000)
        self.assertNotIn('reasoning',projected[-1])
        self.assertEqual(projected[-1]['reasoning_details'],details)
        self.assertEqual(s.messages,before)
        validate_messages(projected)

    def test_encrypted_or_different_details_are_not_modified(self):
        for details in ([{'type':'reasoning.encrypted','data':'opaque','signature':'sig'}],
                        [{'type':'reasoning.text','text':'different'}]):
            message={'role':'assistant','reasoning':'text','reasoning_details':details}
            self.assertEqual(request_message(message),message)

    def test_schema_and_system_are_counted_once(self):
        import json
        s=Session.create(Path.cwd(),'model','x'*100)
        s.messages.append({'role':'user','content':'task'})
        exact=len(json.dumps([],ensure_ascii=False))+len(json.dumps(s.messages,ensure_ascii=False))
        messages,_=project_context(s,[],exact)
        self.assertEqual(messages,s.messages)
