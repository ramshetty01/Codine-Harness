# Structured run outcomes (A4)

Each model turn writes `Session.last_run` and emits one structured `run_end` event.
The existing `run_turn()` return strings remain compatible with the CLI and eval
runners. Records include a run ID, timestamps, outcome, state, reason, retryable
flag, verification status, task-plan revision/state, unknown-action flag,
pending-charge flag and persistence outcome.

| Trigger | State | Reason |
| --- | --- | --- |
| Model ends its response | awaiting_input | model_stop |
| Output or model-step allowance reached | paused | output_limit / step_limit |
| Repeated tool loop | paused | loop_limit |
| Checked restart requested | restarting | restart_requested |
| Ctrl+C during model, summary, tool or approval | cancelled | user_cancelled |
| Spending reservation blocks work | blocked | budget_blocked |
| Context cannot fit or compact safely | blocked | context_limit |
| Provider call fails | failed | provider_error |
| Session save fails | failed | persistence_error |
| Other tool/runtime failure | failed | execution_error |
| Preflight/configuration failure | blocked | configuration_error |

`completed` remains the legacy outcome string for a model stop; it does **not**
assert the user's task succeeded. `verification` is always `unverified`. External
graders may independently establish a task result, but neither assistant text nor
plan evidence text is promoted to verified success here.

Task states are separate: `no_plan`, `blocked`, `unresolved`, `reported_complete`
or `stale_plan`. All-completed items yield `reported_complete` only when the plan
revision changed during this turn. An unchanged completed plan is `stale_plan`,
because a previous task's plan does not prove a new user request was completed.
Pending and blocked items remain visible even when the model says “all done.”

`retryable` is a continuation hint, **not permission to automatically replay** a
request or tool. Only bounded normal continuation/restart cases can set it true.
Provider failures, cancellation, loop limits, unknown actions and pending charges
never request an automatic retry. The runner does not retry inference.

The agent owns error/cancellation journaling and emits the final event; the CLI no
longer duplicates journal repair or `run_end`. Unfinished tool batches are repaired
with explicit unknown results, without replay. A successfully saved outcome can
be inspected after resume. A persisted `running` marker after an abrupt crash is
not a completed outcome and must not be interpreted as success.

If saving was the primary failure, the wrapper does not retry the failed save.
Its event has `persistence: not_attempted`, and the last durable snapshot remains
the recovery source. If saving the report for a different error fails, that one
attempt records `persistence: failed` in memory/event and the **original exception**
is re-raised. A renderer failure during error reporting likewise cannot mask the
original exception. No failure path claims metadata was durably saved when it was
not. As with all atomic file writes, a failure after replacement/fsync can leave
an uncertain on-disk latest snapshot; inspect the saved session on recovery.

Regression coverage in `tests/test_run_states.py` includes unresolved/stale plans,
all bounded stop reasons, provider/context/budget failures, one-event JSON output,
primary/secondary persistence failures, tool/approval and summary cancellation,
and rejection of a corrupted saved record claiming verified success. These are
fake-provider checks with no API spending.
