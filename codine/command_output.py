"""Incremental decoding and cross-chunk redaction for bounded command output."""
import codecs


class OutputEvents:
    def __init__(self, callback=None):
        self.callback = callback
        self.decoders = {name: codecs.getincrementaldecoder('utf-8')('replace')
                         for name in ('stdout', 'stderr')}

    def feed(self, stream, data):
        if self.callback:
            text = self.decoders[stream].decode(data)
            if text:
                self.callback({'stream': stream, 'text': text, 'final': False})

    def finish(self):
        if self.callback:
            for stream, decoder in self.decoders.items():
                self.callback({'stream': stream, 'text': decoder.decode(b'', final=True), 'final': True})


class OutputRedactor:
    """Keep secret-length tails per stream until matching can be decided."""
    def __init__(self):
        self.pending = {'stdout': '', 'stderr': ''}

    def feed(self, event, redactor):
        stream = event['stream']
        text = self.pending[stream] + event['text']
        final = event.get('final', False)
        cut = len(text)
        if not final:
            for secret in redactor.secrets:
                for length in range(1, min(len(secret), len(text) + 1)):
                    if text.endswith(secret[:length]):
                        cut = min(cut, len(text) - length)
        for secret in redactor.secrets:
            start = text.find(secret)
            while start >= 0:
                if start < cut < start + len(secret):
                    cut = start
                start = text.find(secret, start + 1)
        self.pending[stream] = text[cut:]
        return {**event, 'text': redactor.text(text[:cut])}
