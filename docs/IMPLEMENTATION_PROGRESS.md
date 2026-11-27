# Roadmap implementation checkpoint

The authoritative work list is [ISSUES.md](ISSUES.md), linked to 91 issues in
eight GitHub milestones. Issues and commits use the `ramshetty01` account.
Four workstreams run concurrently in isolated Git worktrees; integration happens
on `main` after checks. This describes implementation evidence, not parity with
Pi or Claude Code.

## Landed capabilities

- Model-specific context estimates and output reservation, plus `/context`.
- Explicit, hashed, bounded repository staging with excluded private paths.
- Real Docker repository commands with output/timeout/resource boundaries.
- Bounded dependency preparation with explicit setup-network opt-in and verified
  cache identity; linked dependency inputs and cache paths are rejected.
- User-owned command allow/ask/deny rules scoped to executor/project/directory,
  with explainable decisions and protection from project environment redirection.
- Adversarial regression fixes for linked-file read exposure and terminal control
  characters; these enforce boundaries rather than promise prompt-injection immunity.
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

The integrated checkpoint passed 201 tests with optional Docker checks enabled,
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
| M2 Sandboxed execution | Broader filesystem/network and file-operation policy scope, live output/cancellation and managed background jobs |
| M3 Long-task reliability | Semantic/active-turn compaction, persistent task plans, artifact handling and consistent cancellation/retry states |
| M4 Daily terminal workflow | Steering/queues, richer repo navigation and changesets, session UX, streaming and shared interactive budgets |
| M5 Customization | Hierarchical trusted configuration, skills/templates, reviewed memory and extension lifecycle |
| M6 Integrations | MCP, SDK/RPC, deferred discovery, provider/auth integration and measured caching/fallback |
| M7 Parallel work | Agent-side workers, isolated edits, shared supervision/budgets, task dependencies and session branching |
| M8 Distribution | Keep installation and supported-platform checks passing; optional surfaces need specific acceptance scope |

None of the eight milestones is complete merely because its first issues have
landed. Close each milestone only after its issues and its end-to-end acceptance
gate pass. Preserve user changes, credentials, local sessions and historical
failed reports throughout further work.
