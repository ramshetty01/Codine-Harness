# Notebook cells

Codine understands notebook format 4 (`.ipynb`) as a sequence of code, Markdown
and raw cells. It does not start kernels or execute cells. PDF, Word, images and
older notebook formats are not covered by these tools.

`read_notebook(path, cell=0)` reads one cell's source and returns its type, index,
whole-file SHA256 and total cell count. Indices start at zero. Follow `next_page`
with the returned `expected_sha256` to continue a long cell or move to the next
cell. Pages default to 12,000 characters, maximum 24,000. Files use the existing
2 MB limit and must contain at most 1,000 cells. Stored rich outputs are counted
but not rendered: HTML, JavaScript, embedded images and notebook attachments never
execute in the terminal. Notebook source remains untrusted project content.

`edit_notebook_cell(path, cell, new_source, expected_sha256)` replaces one cell's
source after the entire current source has been read with `read_notebook`. A
partial or stale read is insufficient. The preview and normal file permission
policy apply. New source is limited to 64,000 characters. The editor preserves
other cells, cell IDs and metadata as JSON values. String/list source forms are
preserved. JSON whitespace may change across the file and is included in the diff.
For an edited code cell, old outputs and the execution count are cleared so they
cannot masquerade as results from the new code. No new results are generated.

Changes use normal file checkpoints and the current turn's changeset. `/changeset`
shows them and `/rewind ID` can restore the original bytes when versions still
match. Intervening edits cause refusal. This has the same documented concurrent
writer limitations as other Codine file edits.

Notebook read coverage is bounded to 64 cell/version entries and belongs to the
current session in this process. Restart, eviction or switching sessions requires
reading again. Notebook reads do not grant raw text edit or file deletion
permissions; those tools retain their own read/inspection requirements. A notebook
edit clears notebook coverage, so subsequent cell edits must read the new file
version. Planning mode permits reads only. Private paths, links and configured
read/write policy restrictions are enforced through the existing workspace.
