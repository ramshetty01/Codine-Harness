# Bounded tool-output artifacts

Interactive Codine saves tool results longer than 12,000 characters as immutable
local snapshots and sends a head/tail preview plus an artifact ID to the model.
Known credentials are redacted before snapshot creation. `read_artifact` pages the
full result in slices of up to 6,000 characters. Users can inspect a snapshot
locally with `/artifact ID [OFFSET]`, without a model request. Artifact reads are available in
planning mode but do not grant fresh file-read/edit permissions.

Snapshots are content-addressed SHA-256 JSON records under `.codine/artifacts`,
written atomically with mode 0600. Identical content is reused. Each project is
limited to 64 artifacts, 16 MB total and 2 MB per record. A process lock protects
quota checks and writes. Symlinks, hardlinks, unstable reads and hash mismatches
are refused. Existing artifacts are not silently evicted: archived references
remain valid until the user explicitly removes local state.

If storage is unavailable or full, the result explicitly says that only its
excerpt was retained; it never claims the complete output was saved. Inspect
storage before repeating a command that may already have produced side effects.
These limits concern captured output; an underlying command may already have hit
its own output cap, and an artifact cannot recover bytes never captured.

File-read snapshots retain their source path/hash. Reading one checks the current
file and reports `source_changed`; missing or unreadable current files count as
changed. Always re-read the project file before editing. Artifacts are historical
observations, not a current filesystem cache. Repeated-tool-loop detection still
uses the original result with volatile timing removed, so changing artifact IDs
does not evade the loop limit.

The terminal engine enables offloading and the read tool together. Offline/live
evaluation engines without that tool continue using their existing bounded
results; never generate artifact references that the model cannot resolve.
