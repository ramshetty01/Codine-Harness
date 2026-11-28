# Session forks and conversation navigation (S5)

`/fork [TITLE]` creates and activates a child session at the **complete current
tip**. It copies conversation history, model/provider/executor choice, task plan,
active context boundary, semantic summary and settled accounting. Subsequent
messages in either session do not change the other's archive. Forking makes no
model request and never replays historical tool calls.

```text
/fork Try a smaller implementation
/tree
/tree 20261128-120000-a1b2c3d4
```

`/tree` shows project-local roots and parent links, depth, title, message count,
active-context start and summary boundary; `*` marks the active session.
`/tree ID` uses the normal `/resume ID` path, including provider/executor
compatibility checks and session locks. You may also use `/sessions`, `/resume`
and `/rename`. Parent sessions remain independently resumable. Missing parents
are labelled unavailable or outside the bounded view, not silently fabricated.

This version forks only a complete tip, not an arbitrary message index. It does
not support merging branches or partially cutting a tool-call/result group.
Fork provenance records the source project, parent ID, root ID, depth, inherited
message count and SHA256 of the redacted source history at fork time. The hash
identifies that historical snapshot; it is not an authorship signature or an
assertion that later credential-redacted copies remain byte-identical. It does
not authorize actions. Session version 1 gains an optional `branch` object with
its own version 1 schema; old sessions without that field load as roots.

## What the child sees

The next request uses the same active conversation projection as the parent at
the moment of the fork. Existing `/clear` boundaries and compaction summaries are
preserved; a fork does not silently restore omitted context or wipe the active
task. The entire archive is still copied, including valid tool pairs and old
reasoning metadata. Summary facts and tool outcomes remain historical data.

File-read/edit guards, notebook/skill access receipts, current check results,
last-run verification status and queued-input delivery receipts are reset. A
fresh Git baseline is captured through the ordinary session-switch path. Current
project instructions are rediscovered; new tools or file changes are not inferred
from historical assistant claims. Process-level, explicitly selected permissions,
executor settings and trusted extension configuration are not expanded by a fork.

Branches share the **same project files and reviewed project memory**. A fork is
not a Git branch, separate checkout, filesystem snapshot or rollback. File edits
made in one conversation are visible to the other; reread current files before
editing. Use Git/worktrees for independent code versions and reviewed changesets
for file recovery. Moving a project with the explicit session migration command
creates a new local root and clears old branch links; migration provenance retains
the source session/project rather than linking unrelated project trees.

## When forks are refused

Finish or recover in-flight turns and pending tool journals first. Run or drop
queued/deferred input; forks do not duplicate or silently abandon it. Finish or
cancel active background jobs and resolve uncertain cleanup. Pending/blocked
local spending, unverifiable or unsettled shared-request ownership, a busy shared ledger or an
unsettled global shared reservation also blocks forking. These checks do not
reconcile unknown actions or charges on the user's behalf.

The parent remains active and locked if child creation fails. Child IDs are
checked under their own session lock, so a collision cannot overwrite an existing
session. A crash after a complete child save can leave a valid child that was not
yet shown as active; inspect `/tree` and resume its ID explicitly. No model or
command work is automatically replayed to recover a fork.

## Spending and usage

A fork **does not create fresh spending allowance**. Settled session spend,
limits, request cap and usage counters are inherited, so the child's `/usage`
includes inherited history. Do not sum parent and child usage as independent
charges. The shared OpenRouter ledger remains the authoritative aggregate across
all sessions, forks and evaluations; it stays at the same configured location
and is not copied, reset, migrated or modified by forking.

OpenRouter forks require the configured shared spending ledger. An embedder using
only independent local session budgets cannot fork: copying those budgets could
multiply available spending across branches. Existing shared state is inspected
under its nonblocking lock, checked for key identity and pending/blocked records,
and left byte-for-byte unchanged. Before the first request, an absent ledger is
allowed only with no recorded spend and no conflicting existing state; the fork
creates neither a ledger nor an allowance. The normal inference path will later
initialize/use the same shared path. Legacy or malformed shared state must be
handled through normal budget inspection/recovery, not reset by a fork.

NVIDIA sessions preserve their existing local accounting metadata but do not
claim provider-side billing enforcement. All paths retain current model/provider
limits and perform no live request during fork or tree navigation.

## Bounds and inspection

Fork depth is limited to 32, and a saved child must fit the existing 20 MB session
load limit. Tree inspection reads at most 200 valid sessions, 1000 directory
entries and 2 MB of JSON in aggregate. It is a bounded ancestry view, not a promise
to enumerate every large archive. Truncation is visible; `/resume ID` can still
open a known session under normal session limits. Corrupt, foreign-project,
symlinked and hardlinked tree entries are omitted. Tree inspection never repairs
pending journals, takes ownership of listed sessions or executes anything.

The optional ancestry metadata is strictly validated when saving/loading. IDs,
project identity, depth, hash shape and inherited complete-message boundary must
be valid. Ancestry records are descriptive provenance; files edited outside the
harness are not cryptographically authenticated.

## Offline acceptance

```bash
python -m unittest tests.test_session_forks tests.test_migration tests.test_session_picker tests.test_transcripts tests.test_semantic_compaction -v
python main.py --check
```

The fork suite covers complete tool-pair and summary inheritance, parent archive
preservation, clearing access receipts, backwards-compatible roots, malformed
provenance, pending work/queue/jobs refusal, synthetic shared spend and unknown
charges, no allowance creation, failed writes and held locks, ID collisions,
missing/bounded tree nodes, depth limits and explicit project migration. Its real
PTY test uses a fake provider to fork, continue the child, inspect ancestry and
return to the parent, proving that navigation sends no extra model requests.
No live API calls or actual charges are involved.

A successful OpenRouter request retains a settled receipt reference in its source
session. Forking verifies that receipt belongs to the parent in the same shared
ledger, then removes request ownership from the child without changing the
receipt, ledger or inherited spend. A recovered turn's cancellation flag alone
does not prevent an explicit idle fork; queue persistence failures and queued
work still block it.
