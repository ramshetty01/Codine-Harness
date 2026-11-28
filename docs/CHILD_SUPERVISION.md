# Parent-owned child supervision

`Supervisor(agent, workspace).run(task, limits=WorkerLimits(...))` is the only
accounted entry point for the read-only worker runtime. It runs one child at a
time, with at most 16 retained child links per parent. It does not automatically
spawn workers or replay failed tasks. The parent must be idle, have settled local
and shared spending, and have no unresolved child link. OpenRouter requires the
same parent shared ledger and remote reserve check used for ordinary requests.

Each child completion calls the parent's `Agent.complete`. The parent reserves,
records usage and settles exactly once. Child accounting snapshots are
observational, never independent spending authority. Unknown cost or partial
stream failure leaves the parent reservation pending and blocks another child.
The selected parent model/output allowance must fit the child's limits; this
path cannot silently change models, endpoints or credentials.

Parent sessions persist a bounded run ID, child session ID, phase and deadline.
Child sessions persist their parent/run identity before the first save. They are
archival and cannot resume independently via `SessionStore.load`. Both records
remain local; task text is not duplicated into supervision metadata. Normal
parent turns refuse to proceed with unresolved children. Explicit
`supervisor.recover(run_id)` retires a dead-process link only after settled
accounting and an available child session lock; it never resumes work, repairs a
child action journal or clears an unknown charge.

Cancellation checks occur before dispatch and at streamed output/tool boundaries.
A deadline timer sets the cancellation event and closes the parent's transport
best-effort. `supervisor.cancel()` does the same. Cleanup closes the child session
store; no independent provider client or allowance is created. After cancellation
closed provider clients may require an explicit application restart.

**Limitation:** this implementation is serial and in-process. A blocking native
call or transport that ignores close cannot be forcibly terminated with a hard
OS-process deadline. The synchronous run remains active until it unwinds, and
its durable link remains running if the process dies. Do not claim hard process
isolation or arbitrary uncooperative-worker termination. All verification uses
fake providers; no live API evaluation has run for this milestone.
