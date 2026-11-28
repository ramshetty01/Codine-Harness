# Milestone 4 acceptance: interactive control and recovery

This offline gate verifies that the terminal user can steer ongoing work, inspect
recorded outputs and related diffs, switch and resume sessions, and recover a
related file-edit set. Model replies are deterministic fakes; terminal input,
file tools, approvals, session persistence, queues and recovery journals are real.
No credentials, inference requests, paid evaluations or Docker engine are needed.
Temporary projects and isolated temporary HOME directories keep the checks away
from real source files and user prompt history.

Run from the checkout with its virtual environment active:

```sh
python -m unittest -v \
  tests.test_milestone4_acceptance \
  tests.test_steering_terminal.SteeringTerminalTests.test_busy_steering_followup_and_single_approval_reader \
  tests.test_steering_terminal.SteeringTerminalTests.test_stop_foreground_command_keeps_followup_and_does_not_run_next_action \
  tests.test_terminal_integration.TerminalIntegrationTests.test_followup_arriving_during_busy_reader_shutdown_runs_once \
  tests.test_changesets.ChangesetTests.test_conflict_in_last_path_preserves_every_file \
  tests.test_changesets.ChangesetTests.test_partial_forward_application_recovers_all_paths \
  tests.test_changesets.ChangesetTests.test_partial_rewind_save_failure_resumes_without_replaying_restored_path
```

The gate consists of seven tests. A skip is not evidence for the skipped behavior;
run it on macOS or Linux with working PTY support. Assertions use actual input
acknowledgements and action-specific approval labels, not a delay intended to
approximate whether the reader is ready. Approval prompts can redraw, so observing
a second generic `Approve?` string alone does not identify a new approval.

| User-visible requirement | Concrete evidence |
|---|---|
| Read, review and apply related changes | The new acceptance test asks two `read_file` calls, builds one `apply_changeset` using their returned hashes, inspects both diffs, and approves both paths. Neither file changes after only the first approval. |
| Browse earlier outputs and diffs | `/tools`, `/tools expand GROUP`, `/details GROUP.1` and `/changeset ID` render the persisted group result and both reverse diffs without another model request. |
| Switch/resume without replay | `/new` starts empty tool history; `/resume ID` restores the exact saved messages. The fake-provider call counter remains three: reads, grouped edit, final answer. |
| Recover the related set | `/rewind ID` previews both paths and collects both approvals before restoring either. Original bytes, including a CRLF file, return exactly; the journal is `rewound` and final session tool-call/result pairs validate. |
| Steer while busy | `test_busy_steering_followup_and_single_approval_reader` queues steering and a follow-up during a blocked fake request. Steering reaches the next safe boundary; the follow-up stays out of that request, later receives its own approval, and queue receipts prevent duplicate delivery. |
| Stop without running subsequent actions | `test_stop_foreground_command_keeps_followup_and_does_not_run_next_action` cancels a real foreground command, records incomplete outcomes, retains the follow-up, and validates the transcript pairs. |
| Do not strand shutdown-boundary input | `test_followup_arriving_during_busy_reader_shutdown_runs_once` uses an explicit reader-shutdown barrier. A follow-up acknowledged during shutdown executes once after the reader joins. |
| Preserve newer work and survive partial recovery | The three changeset boundary tests reject a conflict in the final path before altering any path, recover a partially applied edit set, and resume an interrupted rewind without replaying the already restored path. |

Related feature-specific checks remain in `tests.test_attachments`,
`tests.test_markdown_render`, `tests.test_historyview`, `tests.test_input_settings`
and `tests.test_terminal_integration`. These cover attachment boundaries, streamed
rendering, bounded artifact paging, private history/search, external-editor input
and real restart. They are not replaced by this gate.

Passing this gate establishes the described local interaction and file-tool
recovery workflow. It does not measure model coding quality or prove parity with
Pi/Claude Code. Changesets cover recorded file-tool effects, not arbitrary shell,
Git, background-job or external-service side effects. A multi-file update is
journaled and recoverable; it is not a filesystem-wide atomic transaction.
Snapshots remain historical data, and an approval does not authorize replay of
an unknown outcome. Live model evaluation remains a separate budget-controlled
step.

## Integrated acceptance result

Checkpoint `2ed552e` passed 525 tests with Docker enabled and zero skips.
[Hosted run 46091019842](https://github.com/ramshetty01/Codine-Harness/actions/runs/46091019842) passed all nine supported-platform/Docker jobs. A fresh installed wheel passed 12/12 offline exercises, startup, resource/session and restart checks with network access blocked. No inference calls were made and the real OpenRouter allowance remained unchanged. This gate verifies harness behavior, not live-model solve rate.
