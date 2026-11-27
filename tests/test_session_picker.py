from pathlib import Path
import tempfile
import unittest
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from codine.session_picker import build_session_picker, matching_sessions, session_label
from codine.sessions import Session, SessionStore


class SessionPickerTests(unittest.TestCase):
    def test_search_select_and_cancel(self):
        rows = [{'id': 'a', 'title': 'API repair', 'model': 'alpha', 'messages': 4},
                {'id': 'b', 'title': 'Terminal polish', 'model': 'beta', 'messages': 8}]
        self.assertEqual([row['id'] for row in matching_sessions(rows, 'terminal beta')], ['b'])
        with create_pipe_input() as pipe:
            app = build_session_picker(rows, input=pipe, output=DummyOutput())
            pipe.send_text('polish\r')
            self.assertEqual(app.run(), 'b')
        with create_pipe_input() as pipe:
            app = build_session_picker(rows, input=pipe, output=DummyOutput())
            pipe.send_bytes(b'\x03')
            self.assertIsNone(app.run())
        self.assertNotIn('\x1b', session_label({'id': 'x', 'title': '\x1b[31mname'}))

    def test_title_survives_reload_and_metadata_list(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            store = SessionStore(root)
            try:
                session = Session.create(root, 'offline', 'system')
                session.title = 'Fix parser'
                store.save(session)
                self.assertEqual(store.load(session.id).title, 'Fix parser')
                self.assertEqual(store.list()[0]['title'], 'Fix parser')
                session.title = 'bad\x1btitle'
                with self.assertRaises(ValueError):
                    store.save(session)
                self.assertEqual(store.load(session.id).title, 'Fix parser')
            finally:
                store.close()
