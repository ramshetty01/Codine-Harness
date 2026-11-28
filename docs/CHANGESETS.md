# Related edits and turn checkpoints

Every interactive agent turn gets a distinct changeset ID, the same as its run
ID. File-tool writes, edits, moves and deletes capture originals before changing
files and record the resulting versions. `/changeset` lists the latest 100 sets;
`/changeset ID` shows how reverting that set would affect the current files.
`/rewind ID` reviews and restores a whole set. Existing individual `/changes`,
`/diff` and `/undo` commands remain available.

The model can use `apply_changeset` to propose related whole-file text changes:

```json
{
  "changes": [
    {"path": "package/example.py", "content": "value = 2\n", "expected_sha256": "SHA256_FROM_READ_FILE"},
    {"path": "new_test.py", "content": "assert True\n", "expected_sha256": null}
  ]
}
```

Existing files require complete current reads and matching hashes; new files must
be absent. Paths are canonicalized and duplicates rejected. All diffs are shown
first, then all required path/core approvals are collected. Every path is checked
again after approval, before the first write. A denied approval, invalid argument,
or preflight conflict leaves every file unchanged. Each actual write also checks
its own version; a later external change can interrupt an application already in
progress, leaving a recoverable partial set rather than being overwritten.

`apply_changeset` accepts at most 32 paths, 2 MB per text file, 8 MB of replacement
data, and a 256 KB combined preview. A turn retains at most 32 paths and 8 MB of
original contents. Split larger work across turns. Grouped rewinds support regular
binary files moved/deleted by lifecycle tools as well as text edits. Binary
previews show sizes, hashes and permissions instead of fabricated text diffs.

## Why repeated edits and move chains work

The journal retains each path's **first before-image** and its **latest verified
state**. Two edits to `a.py` preserve its original contents once. A move from
`a.bin` to `b.bin` followed by `b.bin` to `c.bin` records originals for all three
paths: original `a.bin`, absent `b.bin`, absent `c.bin`. Rewind checks all final
states, restores `a.bin`, and removes the checkpoint-owned final destination.
It does not replay old move commands against stale inode identities.

## Recovery protocol

Changesets live in private `.codine/changesets/ID.json` journals. Original bytes
are base64 encoded with a validated hash, mode and size. A pending operation is
saved before mutation. Replacement inode ownership is saved before installing an
atomic text replacement or copying lifecycle bytes. This distinguishes a write
that never happened from a completed write whose final journal save failed.
Owned partial lifecycle copies can be removed while the original is restored.

Rewind first preflights **every path**, including paths already restored during
an interrupted rewind. It rejects newer contents, replacement identities, modes,
links, excluded paths or denied permissions. It shows the combined inverse
preview and collects all approvals before changing files. Each restored path has
its own journal progress and owned replacement identity; re-running `/rewind ID`
can finish a recoverable interruption without replaying completed restorations.
An error is reported honestly; the set is marked `rewound` only after all paths
and the final state are saved.

The file policy and core-file approval rules apply to every affected path.
Planning mode cannot apply or rewind changesets. Newer changes, including
individual `/undo` operations, can invalidate a set's expected versions. Codine
then refuses whole-set recovery; it does not overwrite those changes. Review the
conflict and recover the appropriate individual checkpoint if necessary.

## Boundaries

This is a journaled sequence, **not an atomic multi-file filesystem transaction**.
Another process may edit files during apply/rewind. Preflight catches existing
conflicts before any mutation; per-path checks catch later conflicts and leave
partial recovery state. Abrupt process/power loss in an unjournaled filesystem
window can leave ambiguous temporary/link state requiring manual inspection;
Codine refuses uncertain overwrites. Empty created directories may remain.

Only file-tool effects are tracked. Shell commands, Git operations, background
jobs, external editors and arbitrary filesystem side effects are not rolled back.
Ownership, timestamps, ACLs and extended attributes are not reconstructed. A turn
with no file-tool edits has no changeset. A saved file checkpoint is evidence of
filesystem state, not proof the model completed the requested task or passed tests.

Offline tests cover combined approvals, conflict preservation, repeated edits,
binary move chains, partial applications, interrupted copies, failed progress
saves, resumed rewinds, core/policy restrictions, aliases, linked files, corrupt
before-images and actual agent turn attribution. No inference is needed to review
or rewind a changeset.
