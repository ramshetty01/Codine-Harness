"""Bounded streaming Markdown emphasis, preserving original copyable text."""
import re

# Patterns operate on one bounded line, without nested repetition/backtracking.
INLINE = re.compile(r'(`[^`\n]+`|\*\*[^*\n]+\*\*|\[[^\]\n]{1,512}\]\([^\)\n]{1,2048}\))')
CONTROLS = re.compile(r'[\x00-\x08\x0b-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]')
LINE_LIMIT = 8192


def sanitize(text):
    # Keep tabs/newlines and all printable source characters. Escape sequences
    # cannot execute; remaining printable sequence bytes are harmless text.
    return CONTROLS.sub('', text)


class MarkdownStream:
    """Write each source character once, styling only complete bounded lines."""
    def __init__(self, write):
        self.write = write
        self.pending = ''
        self.fence = None
        self.long_line = False

    def _line(self, text):
        match = re.match(r'^ {0,3}(`{3,}|~{3,})([^\n]*)', text)
        if self.fence:
            char, length = self.fence
            if match and match[1][0] == char and len(match[1]) >= length and not match[2].strip():
                self.fence = None
                self.write([('md.fence', text)])
            else:
                self.write([('md.code', text)])
            return
        if match:
            self.fence = (match[1][0], len(match[1]))
            self.write([('md.fence', text)])
            return
        if re.match(r'^ {0,3}#{1,6}\s', text):
            base = 'md.heading'
        elif re.match(r'^\s*(?:[-+*]|\d+[.)])\s', text):
            base = 'md.list'
        elif re.match(r'^\s*>', text):
            base = 'md.quote'
        elif '|' in text:
            # Tables retain pipes and spacing; no width-dependent rewriting.
            base = 'md.table'
        else:
            base = 'text'
        fragments, end = [], 0
        for token in INLINE.finditer(text):
            if token.start() > end:
                fragments.append((base, text[end:token.start()]))
            value = token[0]
            tone = 'md.inline' if value.startswith('`') else 'md.strong' if value.startswith('**') else 'md.link'
            fragments.append((tone, value))
            end = token.end()
        if end < len(text):
            fragments.append((base, text[end:]))
        if fragments:
            self.write(fragments)

    def feed(self, text):
        # Slice before accumulating, bounding memory even for an oversized chunk.
        for offset in range(0, len(text), LINE_LIMIT):
            self.pending += sanitize(text[offset:offset + LINE_LIMIT])
            while self.pending:
                newline = self.pending.find('\n')
                if 0 <= newline < LINE_LIMIT:
                    line, self.pending = self.pending[:newline + 1], self.pending[newline + 1:]
                    if self.long_line:
                        self.write([('md.code' if self.fence else 'text', line)])
                    else:
                        self._line(line)
                    self.long_line = False
                elif len(self.pending) >= LINE_LIMIT:
                    # Oversized lines stay copyable, with deterministic chunks
                    # independent of incoming provider fragment boundaries.
                    self.write([('md.code' if self.fence else 'text', self.pending[:LINE_LIMIT])])
                    self.pending = self.pending[LINE_LIMIT:]
                    self.long_line = True
                else:
                    break

    def finish(self):
        if self.pending:
            if self.long_line:
                self.write([('md.code' if self.fence else 'text', self.pending)])
            else:
                self._line(self.pending)
        self.pending = ''
        self.fence = None
        self.long_line = False
