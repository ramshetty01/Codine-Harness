# Explicit file attachments

Type `@path/to/file.py` in a prompt to attach its current text snapshot. Start typing
`@` and select a suggested path with the terminal completion menu. Use
`@"path with spaces.txt"` for spaces (double-quoted paths use JSON escapes).
Email addresses are not attachments; use `@@decorator` for a literal at-sign
reference. An explicit missing/invalid file stops the turn rather than silently
sending a prompt without the intended context.

Before sending, Codine shows each selected path, byte count, SHA256, and a bounded
preview. The attached message contains the entire bounded snapshot, marked as
untrusted project data. Known credentials are redacted. The preview shows up to
12 lines/2,000 characters and clearly identifies shortening; this is different
from truncating the snapshot sent to the model.

Limits: eight distinct files, 24,000 bytes per file, 64,000 bytes total. Text only;
binary files, symlinks/hardlinks, excluded credentials/internal paths and denied
files are rejected. Existing file read approval rules apply. Larger files should
use the ordinary paged read tool. Attachment snapshots do not grant edit read
coverage: the agent must call `read_file` before editing current file contents.

Queued follow-ups and steering retain their original references until delivery;
files are read at that safe boundary, not in the input thread. A failed read
does not acknowledge the queued item or send an inference request. Attachments
consume ordinary context budget; no model/context limit is bypassed.

Completion scans only permitted repository names, honors ignore rules and existing
scan bounds, retains up to 2,000 candidates and shows up to 40 matching suggestions.
Its process-local one-second cache is a typing aid, never file-read authority.
Selection revalidates the real path and policy before reading. A path omitted by
completion limits can still be typed explicitly.

References embedded in code examples can be escaped with `@@`; attachment syntax
is explicit and is not inferred from natural-language requests.
