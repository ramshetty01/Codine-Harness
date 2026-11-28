import io
import os
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from codine.config import Redactor
from codine.markdown_render import MarkdownStream, LINE_LIMIT, sanitize
from codine.terminal import Terminal

SAMPLE = '# Result\n\n- **Ready**: `run()`\n1. See [docs](https://example.invalid/docs)\n\n```python\nprint("hello")\n```\n\n| Name | Value |\n| --- | --- |\n| A | 3 |\n> Check the result.\n'


class MarkdownTests(unittest.TestCase):
    def render(self, parts):
        result=[]
        renderer=MarkdownStream(result.extend)
        for part in parts: renderer.feed(part)
        renderer.finish()
        return result

    def test_styles_and_source_copyability(self):
        fragments=self.render([SAMPLE])
        self.assertEqual(''.join(text for _,text in fragments),SAMPLE)
        tones={tone for tone,_ in fragments}
        self.assertTrue({'md.heading','md.strong','md.inline','md.link','md.code','md.fence','md.table','md.quote'} <= tones)
        self.assertIn(('md.code','print("hello")\n'),fragments)

    def test_every_stream_split_matches_complete_render_without_duplicates(self):
        expected=self.render([SAMPLE])
        for point in range(len(SAMPLE)+1):
            self.assertEqual(self.render([SAMPLE[:point],SAMPLE[point:]]),expected)
        self.assertEqual(self.render(list(SAMPLE)),expected)

    def test_fences_literals_partial_final_and_section_reset(self):
        out=[]; renderer=MarkdownStream(out.extend)
        renderer.feed('~~~~text\n**not bold**\n```\n~~~~\nlast')
        renderer.finish(); renderer.finish()
        self.assertIn(('md.code','**not bold**\n'),out)
        self.assertIn(('md.code','```\n'),out)
        self.assertEqual(''.join(t for _,t in out),'~~~~text\n**not bold**\n```\n~~~~\nlast')
        renderer.feed('# next'); renderer.finish()
        self.assertEqual(out[-1],('md.heading','# next'))

    def test_bounded_long_line_preserves_output_and_resumes(self):
        out=[]; renderer=MarkdownStream(out.extend)
        text='x'*(LINE_LIMIT*8+3)
        for char in text: renderer.feed(char)
        self.assertLess(len(renderer.pending),LINE_LIMIT)
        self.assertTrue(out)
        renderer.feed('\n# next\n'); renderer.finish()
        self.assertEqual(''.join(t for _,t in out),text+'\n# next\n')
        self.assertEqual(out[-1],('md.heading','# next\n'))
        self.assertEqual(self.render([text+'\n# next\n']),self.render(list(text+'\n# next\n')))

    def test_terminal_controls_never_reach_writer(self):
        text='a\x1b[2J\x1b]8;;https://evil\x07x\x1b]8;;\x07\u202eb\x9b31m'
        fragments=self.render(list(text))
        rendered=''.join(t for _,t in fragments)
        self.assertEqual(rendered,sanitize(text))
        self.assertFalse(any(c in rendered for c in ('\x1b','\x07','\x9b','\u202e')))

    def test_terminal_stream_redaction_once_and_plain_preservation(self):
        for color in (False,True):
            terminal=Terminal(Redactor(['secret-across-chunks']),interactive=False)
            terminal.color=color
            fragments=[]; terminal.markdown.write=fragments.extend
            out=io.StringIO()
            with redirect_stdout(out):
                terminal.emit('text','# Answer\nsecret-across-')
                terminal.emit('text','chunks\n```py\nprint(1)\n```')
                terminal.emit('end_stream',''); terminal.emit('end_stream','')
            result=''.join(text for _,text in fragments) if color else out.getvalue()
            self.assertNotIn('secret-across-chunks',result)
            self.assertEqual(result.count('[REDACTED]'),1)
            self.assertEqual(result.count('print(1)'),1)
            self.assertEqual(result.count('# Answer'),1)
            if not color: self.assertNotIn('\x1b',result)

    def test_no_color_keeps_plain_markdown(self):
        with patch.dict(os.environ,{'NO_COLOR':'1'}), patch('codine.terminal.PromptSession'):
            terminal=Terminal(Redactor(),interactive=True)
        self.assertFalse(terminal.color)
        out=io.StringIO()
        with redirect_stdout(out):
            terminal.emit('text',SAMPLE); terminal.emit('end_stream','')
        self.assertIn(SAMPLE,out.getvalue())
        self.assertNotIn('\x1b',out.getvalue())

    @unittest.skipUnless(os.name=='posix','requires PTY')
    def test_narrow_pty_code_is_printed_once(self):
        import pty
        import fcntl
        import struct
        import termios
        import select
        import time
        master,slave=pty.openpty()
        fcntl.ioctl(slave,termios.TIOCSWINSZ,struct.pack("HHHH",24,32,0,0))
        self.addCleanup(os.close,master)
        program='''from codine.config import Redactor
from codine.terminal import Terminal
ui=Terminal(Redactor(),interactive=False)
ui.color=True
for text in ["# Result\\n```py\\n", "print('COPY_ME')\\n", "```"]: ui.emit('text',text)
ui.emit('end_stream','')
'''
        env={**os.environ,'TERM':'xterm','COLUMNS':'32'}; env.pop('NO_COLOR',None)
        process=subprocess.Popen([sys.executable,'-c',program],stdout=slave,stderr=slave,env=env)
        os.close(slave); output=bytearray(); deadline=time.monotonic()+10
        try:
            while time.monotonic()<deadline:
                if select.select([master],[],[],.1)[0]:
                    try: chunk=os.read(master,65536)
                    except OSError: break
                    if not chunk: break
                    output.extend(chunk)
                elif process.poll() is not None: break
            process.wait(timeout=2)
        finally:
            if process.poll() is None: process.kill(); process.wait()
        self.assertEqual(process.returncode,0,output.decode(errors='replace'))
        self.assertEqual(output.count(b'COPY_ME'),1)
        self.assertIn(b'\x1b[',output)


if __name__=='__main__': unittest.main()
