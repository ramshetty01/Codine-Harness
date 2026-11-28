# Storage visibility and reviewed retention

```text
/storage
/storage inspect
/storage plan
/storage plan 30 20 16
/storage apply EXACT_PLAN_DIGEST
```

Inspection reports file counts and logical bytes separately for session archives,
output artifacts, single-file before-images (`changes`), grouped before-images
(`changesets`), and retained trash. The total covers these managed categories,
not every file in the project. It never reads credentials, provider budget
ledgers, extension trust receipts, package recovery directories, or user settings.
Inspection never moves records.

`plan AGE_DAYS KEEP_COUNT MAX_MIB` is a dry run. Defaults are 30 days, the newest
20 records **per category**, and at most 16 MiB moved in one operation. Age must be
1–36500 days, keep count 1–1000, and the byte allowance 1–64 MiB. It selects no more
than 100 records, oldest first, skipping records that would exceed the byte cap.
It can select:

- Inactive, unlocked, old sessions without pending/running actions or uncertain
  billing. Sessions carrying shared-request ownership references are retained
  conservatively even if the reference may already be settled.
- Old artifacts with no references in retained sessions, other artifacts,
  before-images/recovery records, or retained trash.
- Old, unreferenced before-images only when already undone or fully rewound.
  Active rollback checkpoints remain visible in usage totals but are not cleanup
  candidates in this version.

The current session, queued input, pending recovery records, live jobs, uncertain
job cleanup, current locks and malformed/unreadable state are never cleanup
candidates. Any other live session in the project blocks planning/apply, even if
it would not itself be selected. A nonempty stored inbox blocks retention. Reference
scans include all retained archives, including sessions being moved to trash:
archiving a session never silently abandons the artifacts it might need later.
Malformed or incomplete inventories fail closed rather than guessing references.

## Apply and recovery

Applying a plan prints its exact selected paths, hashes, identities, age/count/byte
limits and protected counts, then asks for explicit approval. No automatic cleanup
runs at startup or during a model turn. Plan mode permits inspection and dry-run
planning but blocks apply. Declining leaves records at their original locations.
The plan must come from this process, and the inventory plus current session state
must still match after approval; otherwise create a new plan.

Successful apply moves each selected file into a new private directory under
`.codine/storage-trash/BATCH_ID`, keeping a manifest of original paths and hashes.
Native Linux/macOS no-replace rename refuses destination conflicts. Files leave
normal session/history/artifact lookup and associated active-store quotas, but
**retained trash still occupies disk: reported disk bytes reclaimed is zero**.
There is no permanent purge or automatic trash expiry in this version. Session
fork provenance is preserved; `/tree` honestly marks a parent unavailable when
its archive is retained outside normal lookup.

A manifest is written before any move and updated after each move. Interrupted
or failed operations keep a `pending` manifest; later retention planning is
blocked until that recovery state is inspected. If an external write races a
move, the bytes are kept in trash and the operation reports uncertainty. There is
no automatic rollback that overwrites a newly created source path. Stop processes
touching the project and inspect the manifest and both original/trash paths
outside Codine before manual recovery. Preserve all copies; do not delete the
manifest simply to retry. This is retained archiving, not an all-or-nothing
filesystem transaction. No project source files or shell/network effects are
rolled back.

## Coordination and limits

Codine state publishers cooperate through `.codine/storage.lock`: shared,
nonblocking locking wraps session saves and new session-lock acquisition, inbox
replacement, artifact creation/reuse, and both checkpoint-store save paths.
Session journal repair persists through the ordinary session save path. Retention
holds the exclusive lock, then probes and holds every other existing session
lock through the reference scan and moves. It excludes only the active CLI
session's already-held lock after confirming idle state. New sessions cannot
acquire their locks while retention holds exclusivity. A busy operation returns
an explicit error rather than waiting indefinitely.

Lock order is storage lock before newly acquired session/publication locks;
normal operations may already own their session lock when taking shared storage
access. All probes are nonblocking. Cleanup never acquires a provider/shared
budget lock and never calls a shared-lock save while holding exclusivity. Approval
happens outside the exclusive scope, followed by fresh inventory validation.

This coordination covers normal Codine operations using a session lease. It does
not establish leases for arbitrary low-level external callers holding an artifact
ID indefinitely, nor stop manual edits or unrelated processes. Stop those writers
before retention. Descriptor identity/hash checks detect observed races; they are
not a sandbox against a malicious process with the same OS user privileges.

Scans are limited to 5000 entries, 64 MB of reference data, existing per-record
limits (2–24 MB), and five seconds each for inventory/reference analysis. Reaching
a bound blocks cleanup; it does not treat the partial scan as complete. Plans are
kept in memory (at most eight). Unknown temporary files, unsafe links, hardlinks,
shared/readable record permissions, or unreadable retained-trash references also
block a plan. Retained trash is protected by the same internal-state exclusion
as session journals.

```bash
python -m unittest tests.test_storage tests.test_artifacts tests.test_changes tests.test_changesets -v
```

Tests use temporary projects and fake clocks, including a real competing session
process, late references, modified preview/apply records, pending journals,
permission failures, byte bounds, preserved fork ancestry, publication locks,
and retained references. No cleanup runs against a real user repository and no
model API is called.
