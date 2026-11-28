# Move, rename, delete and recover regular files (R3)

The model has four additional tools:

- `inspect_file(path)` returns size, SHA256, permission mode and text/binary kind.
  It reads no source text into the model context and establishes a lifecycle version.
- `move_file(path, destination, expected_sha256)` moves one file to a new name/path.
- `rename_file(...)` is the same operation under a familiar name.
- `delete_file(path, expected_sha256)` removes one file with a recoverable checkpoint.

Scope is regular, single-link project files up to 2 MB, including arbitrary binary
bytes. Directories, symlinks, hardlinks, excluded credential/internal paths and
policy-denied paths are refused. A complete current `read_file` also establishes a
version for text files. Metadata inspection alone does **not** grant text-edit read
coverage. Inspection caches are cleared on session switches; a stale hash or changed
inspected identity requires reinspection.

Every operation previews before changing anything. Text uses a real bounded diff;
binary previews show bytes/hash/mode rather than pretending binary data is text.
Moves check source and destination policies and collect both approvals before any
file mutation. Core-file approval rules remain in force even in auto-edit mode.
The destination must not exist; exclusive creation also catches a destination
appearing after approval. Source identity/content are checked after approval and
again before unlink. Read and write policy checks remain active during recovery.

## Durable checkpoints and interrupted moves

Lifecycle operations always save a version-2 checkpoint before changing files,
even if ordinary edit tracking was not enabled on the Workspace. Checkpoints use
base64 before-images plus SHA256 and mode, so binary content round-trips exactly.
Old text checkpoint records remain compatible. The checkpoint ID is displayed before
mutation and returned on success; `/changes` finds it after an interrupted action.

A move creates the destination exclusively, journals its device/inode ownership
before copying bytes, fsyncs the complete copy, records its completed identity,
then removes the unchanged source. It never uses an overwrite-style rename. The
phases are `prepared`, `copied` and `completed`. A crash can therefore leave both
files, a partial destination, or a completed move whose final phase save failed;
the before-image remains available.

Use `/diff CHANGE_ID` to review the lifecycle checkpoint and `/undo CHANGE_ID` to
recover. Recovery inspects current state, previews the inverse operation and
rechecks after approval. It can remove a checkpoint-owned partial destination,
restore a deleted source, reverse a completed move, or finish an interrupted undo.
Partial-copy removal requires the recorded inode and a prefix of the original
bytes; completed copies also require the recorded final identity. Newer content,
replaced files or conflicting destinations are refused instead of overwritten.
If ownership could not be durably recorded and a file cannot safely be identified,
recovery fails closed and requires explicit manual inspection.

Copies preserve permission bits; restored files have new filesystem identities
and timestamps. Ownership, extended attributes, ACLs and directory metadata are
not checkpointed. Newly created destination directories may remain empty after
failure/recovery. This is recoverable file manipulation, not a filesystem-wide
transaction or protection against every hostile concurrent filesystem operation.
Shell side effects remain outside the checkpoint system.

Tests cover binary/text versions, binary previews, mode preservation, full forward
and reverse recovery, both approvals, core/read/write policy boundaries, links and
excluded paths, stale-after-approval source/destination changes, checkpoint-save
failure, failure after deletion, partial copies and newer-work rejection. No live
model calls or API credits are used by these tests.
