# Explicit read-only worker plans

`/workers` is a user command, not a model tool. It stores a bounded dependency
plan in the current session. Planning and dispatch each show the exact proposed
content and ask for approval. It never recursively delegates or automatically
continues with another wave.

Example:

```
/workers plan [{"id":"inspect","prompt":"Inspect parser.py and report risks","depends_on":[],"resources":["parser.py"]},{"id":"review","prompt":"Review the earlier observations","depends_on":["inspect"],"resources":["parser.py"]}]
/workers run {"max_steps":4,"max_total_tokens":12000,"max_seconds":60}
/workers status
/workers run
```

A plan has at most eight tasks, each with a unique ID, a prompt up to 1024 UTF-8
bytes, and one to eight project-relative resource paths. Unknown dependencies,
cycles, private paths, traversal, and overlapping resources without a dependency
ordering are rejected. Paths use component boundaries and normalized case for
conflict comparisons. `.` covers the project. Resources declare cooperative
ownership, not additional read permission or a restriction on which allowed
files a worker can read.

Each run dispatches at most two ready tasks. The parent supervisor retains at most
16 child records; replacing a plan does not reset that bound. Limits are displayed before approval
and persisted with the plan: steps default 6 (maximum 12), total token allowance
12000 (maximum 100000), and seconds 60 (maximum 120). The current parent output
cap is inherited. Requests reserve from the parent's accounting path; child
requests do not mint a fresh dollar allowance. Token estimates are conservative,
not an independent billing measurement. Two read contexts can run concurrently,
but parent provider requests and durable accounting are serialized. Parallel
workers cannot solicit file-read approval on competing terminal threads.

Worker completion means its run returned a completed outcome. It is not a test
verification claim. Observations retain `verified: false` and bounded source
provenance. Prerequisite results are passed explicitly as untrusted observations;
large dependency evidence refuses dispatch rather than silently dropping it.
Paused, failed, cancelled, and interrupted prerequisites block dependents.
Completed tasks are not replayed by `/workers run`, session resume, or recovery.
Creating a replacement plan is a new explicit user decision.

## Checkpoints and recovery

The plan is saved before launch, running state before dispatch, and outcomes after
the wave. The underlying supervisor separately checkpoints each child lifecycle.
`/stop` or Ctrl+C cancels an active wave; `/workers cancel` is also available when
the command dispatcher can receive it. Cancellation is cooperative and closes
provider transport; it is not a hard process kill. Normal cleanup waits for
workers and releases owned resources. Queued input is deferred, not silently
turned into additional worker tasks.

After an interrupted process, `/workers recover` requires ownership of the parent
session and settled shared accounting. It marks unknown running tasks interrupted,
blocks their dependents, and releases only that parent's leases. It never settles
unknown charges or reruns work. An unresolved charge must be reconciled through
the existing budget workflow first. Forks and model migrations clear dispatch
plans; they do not duplicate task ownership.

Private atomic `.codine/worker-resources` records coordinate sessions/processes.
Stale ownership is not expired automatically. Reviewed isolated-source integration
holds the same lock across approval, conflict checking, and application. A worker
cannot acquire ownership midway through that integration. This is not protection
against arbitrary host programs, all ordinary file edits, or a malicious process
running as the same user. P3 supports isolated source snapshots and explicit detached linked Git worktrees.
Worker plans do not automatically integrate either kind of editing tree.

## Offline acceptance

`python -m unittest tests.test_worker_tasks tests.test_supervisor tests.test_subagents tests.test_isolated_worktrees`

Tests exercise actual worker accounting/contexts, two ordered waves, failed and
output-limited prerequisites, no replay, scope conflicts, explicit recovery,
review-time state changes, bounded selectable limits, actual reviewed snapshot
integration, and a real SDK/CLI subprocess that dispatches and resumes a plan.
Scripted providers make these lifecycle checks; they do not establish live model
quality or independently verified task success.
