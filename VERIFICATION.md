# MVP verification report

Verified locally on 22 November 2026 (Asia/Kolkata), macOS, Python 3.14 in the repository's `.venv`. Runtime implementation checkpoint: **`66aac85`**. Original PRD checkpoint: **`a007239`**.

**Result: the documented MVP acceptance checks passed.** This covers the bounded Python harness described in the PRD, not full Pi or Codex parity.

## Reproducible local checks

```bash
.venv/bin/python main.py --check
.venv/bin/python -m unittest discover -s tests -v
git diff --check
```

Observed results:

- Startup check: **13 owned Python files, 8 registered tools**, no API request or session write.
- Test suite: **42 tests passed**, last full run 6.812 seconds.
- Whitespace check: passed.
- Exact configured credential-value scan across implementation, tests, and documentation: passed; no secret values printed.
- `example.py` compared byte-for-byte against the pre-work backup: unchanged by this implementation. Its pre-existing Git modification is intentionally not committed.
- `.env`, `.codine/`, `.sessions/`, virtual environments, and Python caches remain ignored.

The unit suite uses no API credentials. The retained historical OpenRouter picker has four standalone UI/filter tests; the local `/model` test now exercises the new NVIDIA CLI. It is not wired into this runtime.

## Live NVIDIA workflow

Command:

```bash
.venv/bin/python tests/live_smoke.py
```

The test created a disposable project with a README marker, then used the configured NVIDIA model `nvidia/nemotron-3.5-super-vl-120b-a12b`. It approved the test's small file and command operations through an injected test callback. Normal CLI approval defaults were not changed.

Observed tool sequence:

1. `read_file` and `list_files`.
2. `write_file` created `hello.py` with `print("Hello, Codine!")`.
3. `check_python` reported valid syntax.
4. `run_command` ran the file using the harness interpreter.
5. The model gave its final answer, including the actual README marker.

The test asserted exact created file contents, actual stdout **`Hello, Codine!\n`**, exit code **0**, valid tool-call/result pairing, and identical messages after saved-session reload. The temporary workspace was removed after the test.

Five model calls completed in approximately 1.4, 1.1, 0.7, 1.0, and 1.8 seconds respectively. These are observations from one run, not a latency guarantee. Provider-reported aggregate usage: **9,571 input + 506 output = 10,077 tokens**. No dollar cost was inferred.

## Acceptance traceability

| ID | Evidence | Result |
|---|---|---|
| A01 Multiline input | Real PTY bracketed paste with three lines, including `yes`; fake provider received one message. File stayed absent until a separate approval, which was declined. | Pass |
| A02 Pagination | Read a 50,000-character line across cursors and retrieve the final marker; changed hash refused. | Pass |
| A03 Nested text | Nested Markdown list/read/search with correct line number. | Pass |
| A04 Path policy | Escape paths, credential paths, symlinks, binary files, and hard-linked edits refused. | Pass |
| A05 Exact edits | Approved change, denial, unread region, stale version, and modification during approval. | Pass |
| A06 New file | Nested create, existing-target refusal, denied create, and core approval in auto-edit mode. | Pass |
| A07 Registry | Schema names match handlers; unknown tools, missing arguments, extra arguments, invalid JSON, and bool-as-integer refused. | Pass |
| A08 Streaming | Text/reasoning/usage chunks, interleaved tool fragments, incomplete stream, and truncated tool requests. | Pass |
| A09 Sessions | Saved messages and tool IDs round-trip; configured credential is masked in JSON. | Pass |
| A10 Interrupted actions | Ctrl+C journal repair, saved known results, unknown missing results, failed intent save, failed post-action save, and no replay. | Pass |
| A11 Corruption/locks | Malformed JSON preserved, wrong workspace refused, second writer rejected, atomic-replace failure preserves original snapshot. | Pass |
| A12 Restart | Exec argument checks, syntax rejection, fresh startup probe, and real PTY process replacement retaining the same session/project. | Pass |
| A13 Context | Whole-turn projection and compaction preserve archive and valid pairs; oversized turn rejected; `/clear` keeps saved history. | Pass |
| A14 Execution | Actual cwd/stdout/stderr/nonzero status; key omitted from child environment; execution denial. Live task returned exit 0. | Pass |
| A15 Process limits | Timeout, output overflow, cancellation reaping, and descendant termination verified with harmless processes. | Pass |
| A16 Self-development | Fake-provider agent reads/edits a disposable copy of its registry, checks syntax, requests restart; fresh process successfully dispatches added `ping` tool. | Pass |
| A17 Live provider | Real NVIDIA read/create/check/run/final-answer/resume workflow above. | Pass |
| A18 Repository hygiene | Source baseline backed up locally, scoped commits, ignored private files, credential scan, unrelated sample preserved. | Pass |

Tests are in `tests/test_harness.py`, `tests/test_terminal_integration.py`, `tests/test_nvidia_stream.py`, and the retained `tests/test_model_picker.py`. `tests/live_smoke.py` is opt-in and is not part of unittest discovery.

## Issues found and corrected

- Tests tied to old `main.py` globals were updated to the module boundaries; the tool-round-trip check moved into the agent/session suite.
- macOS temporary paths can resolve through `/private`; restart checks compare canonical project paths.
- Session loading now acquires the advisory lock before reading, preventing a concurrent writer from making the loaded snapshot stale.
- Exact edits require the changed character range to have been returned by a read, not merely a hash of an unseen whole file.
- A checked restart reports invalid syntax as a recoverable error and keeps the existing process alive.
- File and session replacements fsync their temporary files and parent directories.
- Command approvals show argv, working directory, and timeout. Cancellation/timeout kills the process group, including descendants.

## Remaining limits

- Tested on macOS only; POSIX APIs target macOS/Linux. Native Windows support is absent.
- Commands have the user's filesystem/network permissions. File policies and confirmations are not a sandbox, and an approved self-edit can change those policies.
- The model can choose bad code or decline a task despite having tools. The harness records real outcomes but does not guarantee model quality.
- Context uses a character budget and whole-turn trimming, not token-perfect sizing or semantic summaries. Old facts can fall out of active context.
- Session format is linear, with a 20 MB load limit. Snapshotting becomes more expensive as history grows. Begin a new session before reaching the limit; automated rotation is deferred.
- No automatic retry/replay, plugin hot reload, parallel agents, background task queue, image UI, or full-screen transcript.
- Syntax/import checks catch only some broken updates. Keep working Git checkpoints and use `RECOVERY.md` for runtime regressions.
- The crash window between an external side effect and recording its result remains real. Recovery reports unknown outcomes instead of claiming exactly-once execution.
- Credentials are masked when they match configured secret strings. This does not discover every secret a user might paste or a project might contain.

## Terminal workbench update — 22 November 2026

The interface now has a responsive welcome card, teal/amber styling, a slash-command completion menu, colored diffs, explicit review cards, and readable tool summaries. `/details` retains access to the complete last result. Piped output is uncolored, and `NO_COLOR=1` selects monochrome rendering.

The expanded suite passed **47 tests** (7.453 seconds). New coverage verifies menu completion in an actual 52-column pseudo-terminal, Unicode card wrapping, visible command failures, secret redaction, and full-result retention behind shortened previews. Existing real-terminal paste, approval, and process-restart tests also passed. The model adapter and execution policy were unchanged; no additional paid API call was needed for this UI update.

## Documentation-led foundations — 24 November 2026

Research and implementation decisions are recorded in `docs/DEVELOPMENT_PLAN.md`, including the official-source inventory, the difference between full guide reading and selected reference coverage, and explicitly deferred capabilities. `docs/EVALUATIONS.md` defines the offline/live boundary.

**No inference requests were made during this phase.** Documentation retrieval used public URLs, not the model APIs. OpenRouter compatibility is mocked-contract-tested only; it is not yet live-verified. The earlier NVIDIA live result above belongs to the earlier milestone.

Verification:

- **65 automated tests passed** in 7.813 seconds, including existing pseudo-terminal paste/approval/restart checks.
- **12/12 offline scripted exercises passed**; each broken fixture failed its independent behavioral grader, then its trusted reference patch passed. This measures harness/grader integration, not model capability.
- Startup checked **17 Python files and 8 tools**. JSON startup mode emitted valid JSONL.
- Planning filtered tool schemas and rejected forced mutation/command/restart dispatch. Explicit interactive restart retained planning mode.
- Root guidance order, reload, size/symlink rejection, and required approval for guidance edits were tested.
- Repeated call/result pairs stopped before another request; step/loop limits returned failure outcomes. JSON output correlated tool IDs and retained a final outcome without terminal banners.
- Mocked OpenRouter requests used the correct endpoint, excluded NVIDIA template arguments, and preserved complete opaque reasoning metadata.
- Budget tests covered actual charges, invalid costs, exhausted reservations, unknown-cost persistence across resume, blocked retries, and overruns. These are local session controls, not proof of a remote billing cap.
- Legacy NVIDIA sessions without provider/billing fields still loaded.
- Credential scan, documentation-link checks, and preservation of the user's `example.py` passed.

The offline report is `.codine/evals/offline.json` and remains ignored. No untrusted model-generated code was graded: all executed fixtures/reference solutions were authored locally. A real sandbox, dedicated key limit, verified model endpoint, and small compatibility smoke test remain prerequisites for later paid coding evaluations.
