# Codine gaps and development plan

28 November 2026. Based on Codine's implementation and the current official Pi and Claude Code documentation indexes plus focused feature references. This is a capability-level engineering backlog, not a claim to have audited every product release, platform, enterprise setting, or undocumented behavior. Availability of commercial features depends on product version, platform, provider, and plan.

## How to read this comparison

- **Pi core:** a deliberately small, extensible harness. Pi's README explicitly leaves subagents and plan mode to customization. Its tools/extensions run with the process's permissions; project trust is not an OS sandbox.
- **Claude Code:** a broader product with native permission workflows, sandbox options, skills, hooks, MCP, subagents, and additional product surfaces.
- **Codine improvement:** our proposed implementation or reliability requirement. Inclusion does not mean both reference products implement it in precisely this way.

References: [Pi overview](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/README.md), [Pi architecture](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/how-pi-works.md), [Pi security](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/security.md), [Claude feature overview](https://code.claude.com/docs/en/features-overview), [Claude documentation index](https://code.claude.com/docs/llms.txt).

**Priority:** P0 = next reliability milestone; P1 = a capable daily terminal agent; P2 = extensibility and advanced workflows; P3 = optional distribution/platform expansion. Missing functionality is not necessarily the best next investment.

## Existing foundation — do not rebuild

Codine already has a terminal launcher; nine registered tools; multi-step model/tool execution; exact and SHA256-guarded line edits; previews and approvals; enforced planning mode; basic root project instructions; linear saved sessions and crash journals; checked restart; single-file checkpoints/diff/undo; bounded command execution; basic terminal completion; NVIDIA streaming and OpenRouter complete-message transport; an OpenRouter model picker; JSONL one-shot output; context trimming; session budgeting; and a shared budget for evaluations.

Recorded evidence: 114 automated tests passed. The final small live suite completed 14/14 tasks. Across development, 43/46 attempts completed, including three documented failed/incomplete attempts. These are public, small Python tasks, not proof of large-repository performance or parity with either reference agent.

## 1. Execution and permission boundaries

Claude Code documents sandboxed Bash and granular permission rules. Pi does not promise native tool sandboxing; its security model makes trust boundaries explicit. Our general-purpose sandbox is therefore a Codine reliability goal and a comparison against Claude's capabilities, not a feature we should falsely attribute to Pi core.

| ID | Priority | Codine today | Planned capability |
|---|---|---|---|
| E1 | P0 | Docker exists only in evaluation executors | Explicit sandbox mode for normal terminal tasks, with visible effective isolation status |
| E2 | P0 | Evaluator stages Python modules only | Controlled repository staging for selected source/config/test files; protect credentials, internal data, symlinks, and generated directories |
| E3 | P0 | Standard-library Python functions only in Docker | Run real repository test/build commands with working directory, environment, exit status, and bounded output |
| E4 | P0 | No sandbox dependency preparation | Reproducible dependency setup and caches; separate setup access from candidate execution |
| E5 | P0 | Evaluation network disabled; host commands unrestricted after approval | Explicit filesystem/network rules; narrowly scoped credential forwarding when a task requires it |
| E6 | P1 | ask, auto-edit, plan; host commands ask each time | Scoped allow/ask/deny rules and explainable permission decisions; never equate approval with isolation |
| E7 | P1 | Commands collect output before returning | Live bounded command output, user cancellation, and reliable process-tree cleanup |
| E8 | P1 | No background task supervisor | Track long-running tests/dev servers, inspect logs, cancel jobs, and clean them up on exit/restart |

Acceptance: a repository test runs in the sandbox; deliberate access outside declared filesystem/network boundaries is refused; cancellation removes descendants; failure never silently falls back to host execution.

Sources: [Claude sandboxing](https://code.claude.com/docs/en/sandboxing), [permissions](https://code.claude.com/docs/en/permissions), [Pi security](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/security.md).

## 2. Long tasks and context management

Pi documents automatic compaction and branch summarization; Claude documents context management and compaction workflows. Codine currently omits whole older turns, retains its archive, and stops when one active turn becomes too large. Our `/compact` keeps recent turns; it does not generate a summary.

| ID | Priority | Gap and planned work |
|---|---|---|
| C1 | P0 | Add model-aware token/context estimates instead of relying only on character counts; reserve output space |
| C2 | P1 | Add automatic semantic compaction while preserving constraints, decisions, changed files, test results, and unresolved work |
| C3 | P1 | Compact long active turns at valid tool boundaries instead of requiring the user to restart the task |
| C4 | P1 | Keep a persistent task plan with completed, pending, and blocked items |
| C5 | P1 | Add a context inspector showing instructions, recent history, tool-output size, summary, and remaining budget |
| C6 | P1 | Keep large output in bounded local artifacts and send useful excerpts; detect stale file summaries/read caches |
| C7 | P2 | Define a reviewed memory policy for reusable project facts, separate from a conversation summary |

Acceptance: a long task continues after compaction, remembers its requirements, maintains valid tool-call/result pairs, and neither replays completed side effects nor invents test results. Measure summary cost and factual retention.

Sources: [Pi compaction](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/compaction.md), [Claude context overview](https://code.claude.com/docs/en/context-window.md), [Claude memory](https://code.claude.com/docs/en/memory).

## 3. Steering and the agent lifecycle

| ID | Priority | Gap and planned work |
|---|---|---|
| A1 | P1 | Accept user steering while a request/tool is running; apply it at a defined safe boundary |
| A2 | P1 | Separate urgent steering from a queue of follow-up tasks |
| A3 | P1 | Provide consistent cancellation during requests, tools, approvals, and compaction; preserve unsent drafts |
| A4 | P1 | Add structured completion reasons and task states beyond a step/loop limit |
| A5 | P1 | Classify transient provider failures and support bounded backoff only when replay and billing are safe |
| A6 | P2 | Add a task supervisor with explicit budgets, stop conditions, checkpoints, and recovery—not an unrestricted infinite loop |

Acceptance: submitting “stop editing; explain the diff” while work is active prevents subsequent edits after the documented boundary, without corrupting the session.

Sources: [Pi agent loop](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/how-pi-works.md), [Claude interactive controls](https://code.claude.com/docs/en/interactive-mode).

## 4. Repository navigation and editing

Codine has literal search, recursive listing, paginated reads, creation, exact replacement, and line replacement. The items below are a practical editing backlog; they should not be interpreted as identical named tools in both reference products.

| ID | Priority | Gap and planned work |
|---|---|---|
| R1 | P0 | Add representative multi-language repository fixtures, beyond small Python packages |
| R2 | P1 | Add glob/name search, regex search, ignore-file support, and more useful large-repository result limits |
| R3 | P1 | Add explicit rename/move/delete tools with preview, stale-state checks, and recovery |
| R4 | P1 | Review related edits across multiple files as a change set; design recovery for partial application |
| R5 | P1 | Add Git status/diff awareness and distinguish pre-existing user work from agent changes |
| R6 | P1 | Recognize common test/lint/type-check commands from repository configuration and collect their real outcomes |
| R7 | P2 | Add symbol/navigation/diagnostic integration when representative tasks demonstrate a need |
| R8 | P2 | Add richer document/notebook handling rather than treating every supported input as plain UTF-8 text |

Acceptance: fix a bug spanning implementation, tests, and configuration; run relevant checks; show the complete diff; leave unrelated modifications intact.

Reference workflow context: [Claude common workflows](https://code.claude.com/docs/en/common-workflows.md), [Pi interactive use](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/usage.md). Exact Codine tool designs are our proposals.

## 5. Sessions, checkpoints, and history

| ID | Priority | Gap and planned work |
|---|---|---|
| S1 | P1 | Searchable/named session picker instead of choosing raw IDs from a text list |
| S2 | P1 | Export/import transcripts with versioned validation and explicit redaction review |
| S3 | P1 | Define handling for moved/renamed projects and session migration |
| S4 | P1 | Turn-level, multi-file checkpoints; previews and conflict-safe rewind |
| S5 | P2 | Session forks and a navigable conversation tree, with clearly defined active context |
| S6 | P2 | Separate conversation rewind from file rollback; do not pretend either undoes shell/network effects |
| S7 | P2 | Retention policies and disk-usage visibility for sessions, output artifacts, and before-images |

Codine already has resume, restart, and single-file undo. Pi's conversation tree and Claude's checkpoint workflows go beyond that; neither should be represented as universal undo of arbitrary external effects.

Sources: [Pi sessions](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/sessions.md), [Claude checkpointing](https://code.claude.com/docs/en/checkpointing.md), [Claude sessions](https://code.claude.com/docs/en/sessions.md).

## 6. Instructions, configuration, and skills

| ID | Priority | Gap and planned work |
|---|---|---|
| I1 | P1 | Hierarchical user/project/subdirectory instructions with documented scope and precedence |
| I2 | P1 | Clear global versus project settings; show which source supplied the active value |
| I3 | P1 | Trust decisions for repository-provided executable resources before loading them |
| I4 | P1 | Discover `SKILL.md` metadata, load full instructions on demand, and resolve supporting references correctly |
| I5 | P1 | Reusable prompt templates/custom commands with arguments |
| I6 | P2 | Reviewed persistent project memory with provenance, editing, and deletion |

Current Codine loads only root `AGENTS.md` and `CODINE.md`, bounded to 16 KB combined. It has no general skill loader. Skills provide instructions; extensions provide executable behavior. Neither should silently grant more permission.

Sources: [Pi skills](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/skills.md), [Pi configuration](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/configuration.md), [Claude memory](https://code.claude.com/docs/en/memory), [Claude feature overview](https://code.claude.com/docs/en/features-overview).

## 7. Extensibility and external tools

| ID | Priority | Gap and planned work |
|---|---|---|
| X1 | P2 | Public, versioned extension API for tools, commands, events, and UI contributions |
| X2 | P2 | Lifecycle hooks with explicit ordering, timeouts, failure behavior, and permission boundaries |
| X3 | P2 | Safe extension discovery and boundary-aware reload so adding a tool need not restart the entire harness |
| X4 | P2 | MCP client support, tool discovery, connection lifecycle, credentials, and per-server trust |
| X5 | P2 | Versioned plugin/package manifests, dependency checks, compatibility checks, installation and removal |
| X6 | P2 | A reusable Python SDK and bidirectional RPC interface; retain the existing JSONL transport |
| X7 | P3 | A curated package registry/marketplace and signed/reproducible distribution |
| X8 | P2 | Deferred tool discovery so large integrations do not place every schema in every request |
| X9 | P2 | Controlled tool orchestration and result filtering for tasks spanning many external calls |

Pi explicitly supports executable extensions, hot reload, RPC, and an SDK. Claude documents hooks, MCP, and plugins. Current Pi documentation also describes built-in MCP connections over stdio and streamable HTTP, with deferred tool discovery and codemode exposure; these are gaps against both products, not merely an extension idea. Codine's internal Python registry and JSONL output are useful starting points, not a complete extension API or RPC protocol.

Sources: [Pi extensions](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/extensions.md), [Pi index](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/index.md), [Pi MCP](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/mcp.md), [Claude MCP](https://code.claude.com/docs/en/mcp.md), [Claude feature overview](https://code.claude.com/docs/en/features-overview).

## 8. Providers, streaming, and cost controls

| ID | Priority | Gap and planned work |
|---|---|---|
| M1 | P1 | Stream OpenRouter responses with correct reconstruction of tool calls and provider reasoning fragments |
| M2 | P1 | Track capabilities per model/endpoint and adapt unsupported parameters before sending requests |
| M3 | P1 | Cached model catalogs and deliberate provider/model selection when metadata is unavailable |
| M4 | P1 | Shared spending controls for normal interactive sessions, not only the evaluation runner |
| M5 | P1 | Report actual input/output/cache/reasoning usage when supplied; expose missing usage as unknown |
| M6 | P2 | Direct provider adapters and local/custom compatible endpoints, with provider-specific compatibility tests |
| M7 | P2 | Secure authentication management and supported provider login flows; API and subscription access are not interchangeable |
| M8 | P2 | Measured prompt caching and explicit fallback policies that preserve budgets and conversation validity |

Pi has broad connection/authentication/custom-provider facilities. Codine currently supports two transport paths: NVIDIA and OpenRouter. Seeing many models in OpenRouter is not the same as supporting every provider protocol. Also, `/thinking` currently toggles display; it is not a model reasoning-effort controller.

Sources: [Pi models](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/models.md), [Claude prompt caching](https://code.claude.com/docs/en/prompt-caching.md). The shared-budget design is our engineering recommendation, not a claim that both competitors use Codine's ledger model.

## 9. Terminal experience and multimodal inputs

| ID | Priority | Gap and planned work |
|---|---|---|
| U1 | P1 | `@file` selection, path completion, and explicit attached-file previews |
| U2 | P1 | Richer Markdown/code rendering while preserving readable streaming and copyable output |
| U3 | P1 | Expand/collapse tool groups; browse more than the last tool result |
| U4 | P1 | Persistent searchable prompt history, external-editor input, and user-configurable key bindings |
| U5 | P1 | Accurate busy/cancel/queue status and context/cost visibility |
| U6 | P2 | Image/screenshot inputs with capability checks and a session-schema migration |
| U7 | P2 | User theme preferences, light/dark/accessibility checks, and broad terminal compatibility |
| U8 | P3 | Additional convenience surfaces such as voice input, where platform support and demand justify them |

Codine already has multiline paste, slash completion, model selection, diffs, a status footer, and basic color controls. These are improvements to an existing UI, not a need to redesign the appearance again.

Sources: [Pi usage](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/usage.md), [Claude interactive mode](https://code.claude.com/docs/en/interactive-mode).

## 10. Parallel work and autonomous workflows

| ID | Priority | Gap and planned work |
|---|---|---|
| P1 | P2 | Read-only subagents with separate context and bounded summaries |
| P2 | P2 | Shared budget/cancellation supervision across all workers |
| P3 | P2 | Isolated worktrees for concurrent edits, with conflict-aware integration |
| P4 | P2 | Task dependency tracking and explicit ownership of shared resources |
| P5 | P3 | Durable background/scheduled work with reconnectable status and logs |
| P6 | P3 | Optional agent teams and messaging after simpler parallel workflows are reliable |

Claude documents native subagents and broader parallel workflows. Pi's minimal core deliberately leaves subagents to customization. Codine should not implement a whole agent-team system before one agent performs representative repository tasks reliably.

Acceptance: cancel a parallel run and all workers stop; their charges stay within one allowance; two edits never silently overwrite each other; failures remain attributable to a worker/task.

Sources: [Pi overview](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/README.md), [Claude subagents](https://code.claude.com/docs/en/sub-agents.md), [Claude worktrees](https://code.claude.com/docs/en/worktrees.md).

## 11. Product surfaces and distribution

These are mainly broader Claude Code product comparisons or Codine distribution work. They are not prerequisites for a good terminal harness.

| ID | Priority | Gap and planned work |
|---|---|---|
| D1 | P1 | Installable Python package/console entry point, version command, upgrade/uninstall workflow, and dependency management |
| D2 | P2 | CI installation/testing matrix across supported Python/OS/terminal versions |
| D3 | P2 | Windows/WSL support or an explicit maintained platform policy; current implementation uses POSIX mechanisms |
| D4 | P2 | Editor integration through the stable SDK/RPC layer |
| D5 | P2 | GitHub/GitLab automation and reviewable PR workflows |
| D6 | P3 | Browser/computer integrations and web-app verification tools |
| D7 | P3 | Desktop/web/mobile clients and remote control |
| D8 | P3 | Organization policy, shared configuration, audit logging, and administration |
| D9 | P3 | Hosted scheduling, team collaboration, and managed deployment |

The existing `codine` launcher is tied to this checkout and its virtual environment. That works locally but is not a general package distribution system.

Source for commercial product surfaces: [Claude Code documentation index](https://code.claude.com/docs/llms.txt). Availability varies; we are not promising to reproduce all of these products.

## 12. Evidence and engineering quality

This is Codine's quality backlog, not a claim to know either competitor's internal test suite.

| ID | Priority | Gap and planned work |
|---|---|---|
| Q1 | P0 | Realistic bug-fix, feature, refactor, test-repair, and recovery tasks from real repositories |
| Q2 | P0 | Separate model-visible checks from held-back final grading; prevent reference/expected-answer leakage |
| Q3 | P0 | Freeze and hash tasks, source versions, execution images, and request configuration for reproducibility |
| Q4 | P0 | Compare the same model and task conditions across harness changes; repeat runs to expose variance |
| Q5 | P0 | Track success, regressions, incorrect edits, tool failures, interventions, latency, actual/unknown cost, and cost per solved task |
| Q6 | P1 | Fault injection for provider disconnects, rate limits, disk failures, corrupted state, cancellation, and process crashes |
| Q7 | P1 | Adversarial repository/tool-output cases and permission-boundary tests |
| Q8 | P1 | Long-session, large-repository, terminal compatibility, and sustained resource-leak testing |
| Q9 | P1 | Private diagnostic bundles, structured error categories, and documented migration/recovery paths |

Existing tests cover parts of Q5–Q7. Expand them rather than treating them as absent. Our final 14/14 suite does not answer whether Codine can reliably modify a substantial application.

## Build order and completion gates

| Milestone | Scope | Completion gate |
|---|---|---|
| 1. Representative tasks | Q1–Q5, R1 | Five realistic repository tasks with independent final grading and reproducible manifests; preserve every attempt |
| 2. Sandboxed repository execution | E1–E5, basic E7 | Real tests/builds work inside the boundary; access/cancellation/cleanup tests pass; no host fallback |
| 3. Long-task reliability | C1–C6, A3–A5 | A task survives compaction and interruption without losing requirements or replaying actions |
| 4. Daily terminal workflow | A1–A2, R2–R6, U1–U5, S1–S4, M1–M5 | A user can steer work, inspect outputs/diffs, switch/resume sessions, and recover a related change set |
| 5. Project customization | I1–I5, X1–X3 | A trusted skill and extension load/reload through documented interfaces with failure isolation |
| 6. External integrations | X4–X6, X8–X9, M6–M8 | One MCP integration and one SDK/RPC client work with explicit permissions and cost accounting |
| 7. Isolated parallel work | P1–P4, S5–S7 | Two workers run without file conflicts or independent budget resets; cancellation reaches both |
| 8. Distribution and optional products | D1–D9, P5–P6, U6–U8, X7 | Ship only surfaces justified by usage; maintain compatibility and recovery tests |

Some installation and UI work can be pulled forward if it blocks real usage. Dependency order matters more than the label: do not enable unattended command execution before its boundary works, or parallel spending before a shared supervisor exists.

## What “comparable” should mean

Use Pi as a reference for a small extensible harness and Claude Code as a reference for integrated developer workflows. Define success through representative task outcomes, controllability, recovery, and cost—not the number of commands or a made-up “percentage complete.”

Model capability remains separate: improving Codine's tools does not make a small model equivalent to a stronger one. Fair harness comparisons use the same model where possible and disclose provider, budget, task, and execution differences. Do not spend the remaining API balance just to increase sample counts; run experiments that answer a specific question.
