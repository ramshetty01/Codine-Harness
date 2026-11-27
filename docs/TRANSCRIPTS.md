# Reviewed transcript transfer

Use `/export history.json` in an interactive Codine terminal. Codine displays the
whole portable document after redaction, then a SHA-256 confirmation command.
Read every message, argument and result before typing that command. Nothing is
written until confirmation. Doing nothing cancels; another preview replaces the
pending review. Exports create new private (0600) files and never overwrite.
The destination's parent directory must already exist. Treat that directory as
trusted. A matching digest confirms the exact preview, even if conversation or
source file contents subsequently change.

Known environment credentials, common token formats, password assignments and
private-key blocks are scrubbed. This is not a universal secret detector: unknown,
encoded or unusual credentials can remain. Do not confirm an unsafe preview.
Use a manually redacted copy of the source history or a new session containing
only shareable content, then preview again. Terminal scrollback contains the
redacted preview; it should also be treated as private. No draft containing raw
history is written automatically.

Use `/import history.json`, review, then type its displayed confirmation command.
Imported messages become historical context in a **fresh session** in the current
project. The current system prompt, root instruction loading, provider, model,
execution selection and policy remain authoritative. The portable system message
is shown for review but replaced on import. Re-read current files before editing:
old tool outputs may describe another project. Import does not call the model or
execute any tool request. Future model requests still use normal policy checks.

The version-1 format contains only `format`, integer `version`, `title`, and
`messages`. It accepts complete tool-call/result pairs, not pending journals.
Unknown message metadata (including opaque reasoning), budgets, grants, jobs,
checkpoints, plans, context summaries and source project paths are not portable.
Limits are 1 MB and 2,000 messages. Input must be a regular file, not a symlink or
hardlink. Duplicate JSON keys, malformed histories and unsupported versions fail.

Pending actions or uncertain/blocked local cost accounting prevent transfer.
Import preserves the current local spending totals and never rewrites the shared
allowance ledger. Only settled session receipt references are dropped for the new
owner; existing shared reservations still block inference. Resolve uncertain
costs in their original session. The imported file cannot supply accounting state.
Session switching clears stale file-read guards and discovered check IDs.

JSON/piped mode cannot confirm transfers. Exports are private local files, not an
automatic upload or a guarantee that sharing the content is appropriate.

Verification: `python -m unittest tests.test_transcripts -v`.
