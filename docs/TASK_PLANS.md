# Persistent task progress

Codine can maintain a small task plan using `update_plan`. `/tasks` displays it;
`/tasks clear` explicitly starts a fresh plan while incrementing its revision.
The plan lives in the session snapshot and survives save/resume/restart. It is
separate from conversation summaries and does not change project files.

Each item has a stable ID, title and status: pending, in_progress, completed or
blocked. Only one item may be in progress. Blocked items require a reason;
completed items require a description of the supporting evidence. Those fields
record the model's assertions—they are not independent proof of successful tests.
Plans never grant command/edit permissions.

Updates carry the current revision and upsert items by ID. Omitted items remain,
so a partial update cannot silently delete unfinished work. Stale revisions,
duplicate IDs, invalid states and oversized plans are rejected before mutation.
Snapshots are saved before an update returns; failed saves restore the previous
in-memory plan. The normal pending-tool journal still records uncertain outcomes.
Plans are bounded to 32 items and 16 KB; use concise task descriptions.

Planning mode permits this session bookkeeping while continuing to block project
writes and commands. Old version-1 sessions without plan fields load an empty plan
at revision zero. The plan must be included as untrusted state in both normal and
compaction context; it remains available even when older conversation turns are
omitted.
