import unittest
from codine.extension_api import ExtensionAPI


class ExtensionAPITests(unittest.TestCase):
    def test_four_surfaces_and_immutable_descriptors(self):
        api = ExtensionAPI(['tools', 'commands', 'events', 'ui'])
        schema = {'type': 'object', 'properties': {'text': {'type': 'string'}}, 'required': ['text'], 'additionalProperties': False}
        api.tool('echo', 'Echo text', schema, lambda args: args['text'])
        schema['properties']['text']['type'] = 'integer'
        api.command('greet', 'Greet someone', lambda text: 'Hello ' + text)
        events = []
        api.on('turn_end', events.append)
        api.panel('status', lambda: 'Ready')
        returned = api.describe()
        returned['registrations']['tools'].clear()
        self.assertEqual(api.invoke('tools', 'echo', {'text': 'safe'}), 'safe')
        self.assertEqual(api.invoke('commands', 'greet', 'Tam'), 'Hello Tam')
        api.invoke('events', 'turn_end', {'outcome': 'completed'})
        self.assertEqual(events, [{'outcome': 'completed'}])
        self.assertEqual(api.invoke('ui', 'status'), 'Ready')
        self.assertEqual(len(api.describe()['registrations']['tools']), 1)

    def test_arguments_rejected_before_handler(self):
        api = ExtensionAPI(['tools'])
        calls = []
        api.tool('number', 'Number', {'type': 'object', 'properties': {'n': {'type': 'integer', 'minimum': 0}}, 'required': ['n'], 'additionalProperties': False}, calls.append)
        for value in ({}, {'n': True}, {'n': -1}, {'n': 2, 'extra': 1}):
            with self.assertRaises(ValueError): api.invoke('tools', 'number', value)
        self.assertEqual(calls, [])

    def test_undeclared_names_duplicate_and_version_refused(self):
        with self.assertRaises(ValueError): ExtensionAPI([], version=2)
        api = ExtensionAPI(['ui'])
        with self.assertRaises(ValueError): api.command('cmd', 'Command', lambda _: None)
        for name in ('../../escape', 'Bad Name', 'x'*25):
            with self.assertRaises(ValueError): api.panel(name, lambda: '')
        api.panel('status', lambda: '')
        with self.assertRaises(ValueError): api.panel('status', lambda: '')

    def test_unsupported_schema_features_refused(self):
        api = ExtensionAPI(['tools'])
        for schema in ({'type': 'object', 'additionalProperties': True},
                       {'type': 'object', 'additionalProperties': False, '$ref': 'other'},
                       {'type': 'object', 'additionalProperties': False, 'properties': {'x': {'type': 'null'}}}):
            with self.assertRaises(ValueError): api.tool('bad', 'Bad', schema, lambda _: None)

    def test_output_and_registration_limits(self):
        api = ExtensionAPI(['ui'])
        api.panel('large', lambda: 'x'*64001)
        with self.assertRaises(ValueError): api.invoke('ui', 'large')
        api.panel('object', lambda: {'not': 'text'})
        with self.assertRaises(ValueError): api.invoke('ui', 'object')
        for n in range(30): api.panel(f'p{n}', lambda: '')
        with self.assertRaises(ValueError): api.panel('overflow', lambda: '')
