# Markdown in streamed answers

Colored interactive terminals now emphasize common Markdown as answer lines
arrive: headings and bold text, inline code, links, block quotes, fenced code,
and pipe tables. List markers and code contents remain literal. Both backtick
and tilde fences are recognized, including longer closing fences. Code is kept
in the terminal's normal foreground for readable contrast on light/dark themes;
fence labels are muted. There is no language-specific syntax highlighter.

The renderer retains Markdown punctuation, URLs, indentation and line breaks.
It adds no code gutters or line numbers, strips no links, and does not generate
OSC hyperlinks. Tables keep their original spacing rather than being expanded
or truncated to fit the current width. Long code lines use the terminal's normal
wrapping, so narrow windows do not lose source text. This is lightweight emphasis,
not a complete CommonMark parser: nested formatting, escaped delimiters, HTML,
images, and reference links are not interpreted.

Colored answers display available fragments immediately, including an unfinished
line when the provider pauses. The renderer retains at most 8,192 characters of
already-printed line context to classify subsequent fragments and recognize
split code fences. It never redraws that prefix. Formatting that becomes known
only later (such as a closing bold delimiter) does not retroactively restyle
already-printed text. Complete incoming lines receive full lightweight styling.
Oversized lines switch to plain incremental output until the next newline.
Finishing flushes any remainder once, without reprinting a formatted final copy.
An unfinished code fence is reset when the answer section ends. Reasoning and
command output retain their existing presentation. Existing secret-redaction
lookahead still takes precedence over immediate display.

Known-secret redaction happens before rendering, including secrets split across
provider fragments. Model control characters cannot become terminal instructions.
ANSI/OSC control bytes and bidirectional override controls are removed; any
remaining printable escape-sequence bytes are harmless literal text. The renderer
never interprets HTML or runs language processors.

Noninteractive output, `NO_COLOR`, and `TERM=dumb` retain the existing plain-text
stream with Markdown intact. JSON mode keeps its existing machine-readable event
format; this renderer never receives or rewrites those events. There is no new
dependency and no provider request associated with rendering.

`tests/test_markdown_render.py` checks source/style snapshots, every two-fragment
split of a representative answer, one-character streams, oversized lines,
incomplete fences, repeated finish calls, split-secret redaction, hostile control
bytes, plain output, and an actual 32-column PTY. These checks establish rendering
behavior, not model answer quality.
