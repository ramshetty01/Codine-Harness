# Roadmap implementation checkpoint

The authoritative work list is [ISSUES.md](ISSUES.md), linked to 91 issues in
eight GitHub milestones. Issues and commits use the `ramshetty01` account.
Four workstreams run concurrently in isolated Git worktrees; integration happens
on `main` after checks. This describes implementation evidence, not parity with
Pi or Claude Code.

## Landed capabilities

- Model-specific context estimates and output reservation, plus `/context`.
- Accounted semantic compaction at complete tool boundaries, durable task plans,
  immutable paged artifacts and explicit stale-source checks.
- Named/searchable sessions, retained interrupted drafts and structured run states
  that keep model completion claims distinct from verified task outcomes.
- Explicit, hashed, bounded repository staging with excluded private paths.
- Real Docker repository commands with output/timeout/resource boundaries.
- Bounded dependency preparation with explicit setup-network opt-in and verified
  cache identity; linked dependency inputs and cache paths are rejected.
- User-owned command allow/ask/deny rules scoped to executor/project/directory,
  with explainable decisions and protection from project environment redirection.
- Adversarial regression fixes for linked-file read exposure and terminal control
  characters; these enforce boundaries rather than promise prompt-injection immunity.
- Bounded streaming, explicit execution profiles for approved network/credential
  access, and supervised background jobs with logs/cancellation/exit cleanup.
- Normal terminal Docker selection, visible isolation, approved snapshots and
  execution settings preserved across restart/resume.
- Installable wheel/sdist, `codine` entry point, isolated installed state and
  resource access, version/upgrade/uninstall documentation.
- Evaluation provenance, public versus held-back final checks, repeated-report
  comparison tooling and explicit missing/unknown metrics.
- Fault injection covering crashes, cancellation, failed persistence and provider
  uncertainty without replaying side effects.
- Content-free private diagnostics and documented compatibility/recovery paths.
- Five pinned, BSD-licensed repository-derived repair exercises, with all reference
  patches checked in Docker and all attempts preserved under ignored local state.

## Evidence and limits

The integrated checkpoint passed 295 tests with optional Docker checks enabled,
plus both repository fixtures and all five repository-derived command tasks. A fresh wheel passed startup, restart, resource/session checks and
12 offline exercises outside the checkout with network access blocked. Five
repository-derived command tasks passed their broken/reference checks; these are
synthetic mutations from one Python repository, not five independently sourced
real-world bugs. They are harness verification, not measured model success.

No inference requests were made during this implementation batch. Existing
OpenRouter budget reservations, spending caps and uncertain-charge handling remain
in force. Live evaluation comes after offline gates, uses the guarded runner and
must retain every attempted result, including failures.

CI exposed an undeclared test dependency and a metrics integration indentation
error. Both were fixed, with fresh-environment tests and full local integration
checks. The hosted acceptance run [46079252471](https://github.com/ramshetty01/Codine-Harness/actions/runs/46079252471)
passed all eight Linux/macOS × Python 3.11–3.14 jobs and the Docker job.
Later changes receive their own checks; a local pass does not substitute for
the supported-platform matrix.

## Remaining milestone gates

| Milestone | Remaining acceptance work |
|---|---|
| M1 Representative tasks | Multi-language/multi-repository breadth, annotated incorrect edits/interventions, repeated controlled live comparisons |
| M2 Sandboxed execution | Gate passed: 121 checks with zero skips, real build/smoke, and five repository fixtures; see MILESTONE2_ACCEPTANCE.md |
| M3 Long-task reliability | Live long-task/summary-quality acceptance and inference retries only with verified billing/replay safety |
| M4 Daily terminal workflow | Related change sets, file attachments, richer rendering/history, full usage visibility, symbol/document workflows |
| M5 Customization | Hierarchical trusted configuration, skills/templates, reviewed memory and extension lifecycle |
| M6 Integrations | MCP, SDK/RPC, deferred discovery, provider/auth integration and measured caching/fallback |
| M7 Parallel work | Agent-side workers, isolated edits, shared supervision/budgets, task dependencies and session branching |
| M8 Distribution | Keep installation and supported-platform checks passing; optional surfaces need specific acceptance scope |

None of the eight milestones is complete merely because its first issues have
landed. Close each milestone only after its issues and its end-to-end acceptance
gate pass. Preserve user changes, credentials, local sessions and historical
failed reports throughout further work.


## Long-task checkpoint

An offline stress attempt passed 300 turns, a 5,000-file repository, 50 child
processes and 59 synthetic semantic summaries. All 601 archived messages remained
valid; open descriptors were 4 before and after, with only 32 retained traced bytes
in the repeated read loop. See [STRESS_TESTING.md](STRESS_TESTING.md) for scope.
This is harness evidence, not live-model quality or indefinite leak-freedom.

Provider failures now have sanitized classifications and recovery advice.
Metadata GETs have bounded retries; inference retries remain disabled when charge
or replay safety cannot be proven. A5 stays open for that explicit limitation.
No live inference was used in this checkpoint either.

## Repository workflow and shared allowance checkpoint

The next four parallel issues added bounded glob/regex repository search (R2),
read-only Git status/diffs and session baselines (R5), scoped file-operation policy
(E6), and an OpenRouter allowance shared by interactive sessions and evaluations
(M4). See [REPOSITORY_SEARCH.md](REPOSITORY_SEARCH.md),
[GIT_AWARENESS.md](GIT_AWARENESS.md), [COMMAND_POLICY.md](COMMAND_POLICY.md), and
[SHARED_SPENDING.md](SHARED_SPENDING.md).

Permission checks cover Docker staging and automatically loaded guidance as well
as explicit file reads/writes. Git baseline metadata does not establish exclusive
authorship; only current-byte matches against this session's file-tool records
are attributed. Unknown or shell-created changes remain explicit. Repository
search limits scans and isolates potentially pathological regular expressions.

The shared spending migration preserves the existing ledger rather than resetting
it. Unknown provider charges block the allowance across sessions. Owner-bound
request IDs and durable settlement receipts permit recovery without double
charging. This is an installation/state-root allowance domain, not tamperproof
account-global billing. No live inference was used to implement these features.

The sandbox milestone gate passed: 121 checks with no skips, an actual container
build/smoke test, and five repository repair fixtures. See
[MILESTONE2_ACCEPTANCE.md](MILESTONE2_ACCEPTANCE.md) for reproducible evidence and
its Docker/Python scope. A 336-test integration run passed before the final Git
filter regressions and build test; targeted Git tests then passed 9/9.

## Daily workflow continuation

The next parallel batch adds reviewed transcript export/import (S2), OpenRouter
stream reconstruction (M1), configured repository check discovery/execution (R6),
and recoverable file lifecycle operations (R3). A platform-policy audit (D3) also
adds an early unsupported-platform error and a maintained matrix/WSL policy.

- Transcripts carry validated historical conversation, not executable journals or
  permission settings. Confirmation binds to the exact reviewed/redacted bytes.
  Import creates a new session, preserves spending controls and captures a fresh
  Git baseline.
- OpenRouter streams text while withholding tool dispatch until the complete
  batch is validated. Ordered provider reasoning state and final cost accounting
  remain intact; interruptions and missing costs never become assumed free calls.
- Repository checks come from explicit configuration, with source fingerprints,
  existing execution permissions and real exit-status reporting. Docker checks
  bind discovery to the approved snapshot's source hash. Discovery is not success,
  and unavailable runtimes are recorded as unverified.
- Native Windows remains unsupported. The tested macOS/Linux Python matrix is
  explicit; WSL2 is experimental. Unsupported launchers fail before runtime state
  or provider initialization.

Details: [TRANSCRIPTS.md](TRANSCRIPTS.md),
[OPENROUTER_STREAMING.md](OPENROUTER_STREAMING.md),
[PROJECT_CHECKS.md](PROJECT_CHECKS.md), and [PLATFORMS.md](PLATFORMS.md).
No inference was used for this development work.

Final integration for this continuation passed **381 tests with Docker enabled and
zero skips**. A rebuilt installed wheel passed console/module startup, resource,
session-location and restart checks plus 12/12 offline exercises outside the
checkout with network blocked. File lifecycle details and limitations are in
[FILE_LIFECYCLE.md](FILE_LIFECYCLE.md). The previous hosted matrix run
[46084628573](https://github.com/ramshetty01/Codine-Harness/actions/runs/46084628573)
passed; this newer code receives its own hosted run after push. These are harness
checks, not a live-model score.

## Steering, relocation and model selection checkpoint

A1/A2 add separate durable steering and follow-up lanes, one busy terminal reader,
approval handoff, explicit queue review/removal and safe-boundary delivery.
Recovery requires `/next` before sending old pending work. Streaming callbacks and
foreground process cancellation are wired through the same stop event; a provider
that supplies no callbacks can still wait until return/timeout. Skipped obsolete
tool calls do not trigger repeated-action loop detection.

S3 adds digest-confirmed offline project/session relocation with a fresh session
owner, preserved archive/spending, pending-state refusal and no tool/queue replay.
M3 caches bounded public model metadata with visible freshness and explicit stale
selection. M2 adapts requests using model/endpoint capability evidence or explicit
user declarations; required support is checked before reservation. Request plans
are immutable between preflight and dispatch, preventing a second declaration
read from creating a false pending charge. Stop requests arriving during balance
metadata lookup release the proven-unsent reservation.

Model file tools protect managed state and the user capability declaration even
when the selected workspace is a broad home directory. Host commands retain their
separately authorized OS permissions.

Final integration passed **424 Docker-enabled tests with zero skips**, including
real PTY input/approval/cancellation flows. A rebuilt installed wheel passed
startup, session/resource/restart checks and 12/12 offline exercises with networking
blocked outside the checkout. Previous hosted run
[46086265609](https://github.com/ramshetty01/Codine-Harness/actions/runs/46086265609)
passed all matrix jobs; this new checkpoint receives its own CI run.
No inference calls were made and the real allowance remained unchanged.

See [STEERING_AND_QUEUE.md](STEERING_AND_QUEUE.md),
[SESSION_MIGRATION.md](SESSION_MIGRATION.md),
[MODEL_CAPABILITIES.md](MODEL_CAPABILITIES.md), and
[MODEL_CATALOG.md](MODEL_CATALOG.md). Follow-ups run after a completed conversation
turn; this does not independently verify completion of the underlying coding task.

## Attachments, rendering, history and usage checkpoint

U1 adds explicit bounded `@file` snapshots, ignored/private-path-aware completion,
quoted path handling, redacted previews and read-policy checks. Attachments do not
grant edit permission; the edit tool must still read and version-check its source.
U2 styles Markdown without changing its copyable source, hides terminal controls
and streams incomplete lines immediately. U3 browses durable tool groups and pages
archived results without replay or fresh workspace reads. See
[FILE_ATTACHMENTS.md](FILE_ATTACHMENTS.md), [TERMINAL_MARKDOWN.md](TERMINAL_MARKDOWN.md)
and [TOOL_HISTORY.md](TOOL_HISTORY.md).

CI found two busy-to-idle handoff races: slash commands could be rejected by the
busy reader, and a late follow-up could remain queued after the last queue check.
Commands now transfer to the main input loop; the follow-up check runs after the
reader joins. Deterministic PTY tests exercise both boundaries. Streaming also
holds only possible known-secret prefixes, avoiding an unrelated short answer
being hidden until the provider finishes.

M5 adds `/usage`: actual reported counts, per-field coverage and explicit unknown
values. Cache and reasoning counts are subsets, not extra totals. Returned usage
persists even when cost settlement fails; unresolved charges remain blocking.
Session loading/saving validates detailed counters. See [USAGE.md](USAGE.md).

The hosted checkpoint [46089278184](https://github.com/ramshetty01/Codine-Harness/actions/runs/46089278184)
passed all supported-platform and Docker jobs. Subsequent accounting, redaction
and changeset changes receive their own integration checks. A wheel rebuilt at
`6cf7e9d` passed installed startup/resource/session/restart checks and 12/12 offline
exercises with networking blocked. No inference calls were made; the existing
OpenRouter ledger remains unchanged.

The `6cf7e9d` integration passed **477 tests with Docker enabled and zero skips**.
Hosted run [46089628960](https://github.com/ramshetty01/Codine-Harness/actions/runs/46089628960)
also passed all nine jobs. U1/U2/U3/M5 acceptance is verified at this checkpoint.

R4/S4 add related-edit previews and approvals, per-turn net file checkpoints,
`/changeset` review and `/rewind` recovery. Repeated edits and move chains retain
original before-images; partial application is journaled. Review found a write
race during ownership journaling; `02298cb` rechecks after journal persistence and
preserves a conflicting external edit. This remains a non-atomic multi-file
operation, not a transaction or a shell/Git rollback. See [CHANGESETS.md](CHANGESETS.md).
The follow-up review has its own targeted tests and hosted validation.
