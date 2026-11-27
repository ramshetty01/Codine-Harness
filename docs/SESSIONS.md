# Finding and naming sessions

Use `/rename Fix parser edge cases` to give the current session a title. Titles
are session metadata, limited to 120 printable characters and passed through the
configured secret redactor before saving. Failed saves restore the previous name.
Old snapshots without a title remain compatible and display as Untitled.

In an interactive terminal, `/sessions` opens a searchable picker. Type words to
filter names, model IDs or session IDs; use Up/Down to move, Enter to resume and
Esc or Ctrl+C to cancel. `/sessions parser` starts with that filter. `/resume`
without an ID opens the same picker. Session labels show name, update time, message
count, model and ID; transcript contents are not searched or displayed.

Choosing a session keeps existing provider and execution-mode checks. If the
saved session needs another provider/image/executor, start `codine --resume ID`
to restore those settings deliberately. Canceling the picker does not switch or
rewrite a session. In a noninteractive terminal `/sessions QUERY` prints matching
metadata instead of waiting for picker input; `/resume ID` remains available.

The picker changes conversation state only. It does not rewind files, reset
billing, or alter command permissions. Multi-file rewind and project migration
are separate roadmap items.
