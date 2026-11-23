"""Versioned JSONL output adapter; shares agent events with the interactive UI."""
import json
from .terminal import safe_text


class JsonTerminal:
    interactive = False

    def __init__(self, redactor):
        self.redactor = redactor
        self.sequence = 0
        self.last_result = None
        self.state = lambda: {}

    def emit(self, kind, data):
        self.sequence += 1
        if kind == 'result':
            self.last_result = data
        print(json.dumps({'version': 1, 'sequence': self.sequence, 'event': kind,
                          'data': self.redactor.data(data)}, ensure_ascii=False), flush=True)

    def write(self, text=''):
        self.emit('notice', safe_text(text))

    def welcome(self, project, session, model):
        self.emit('session', {'project': str(project), 'id': session, 'model': model})

    def approve(self, description):
        self.emit('approval', {'description': description, 'decision': 'declined', 'reason': 'Noninteractive mode'})
        return False

    def details(self):
        self.emit('details', self.last_result)

    def prompt(self):
        raise EOFError()
