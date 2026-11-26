# Codine: documentation-led development plan

24 November 2026. Next release: **offline foundations, then paid evaluations**.

## Outcome

Build a terminal coding agent that completes small repository tasks predictably, explains its work, preserves recoverable state, and can be evaluated before we spend the $5 OpenRouter balance. This is a Python implementation informed by Pi, Claude Code, and Codex, not a claim of feature parity or a copy of their source.

This milestone makes **zero inference requests**. Fetching public documentation does not use the model account. Passing a scripted offline exercise establishes harness/grader correctness, not model coding ability.

## Research scope and sources

The documentation sets include very large API references, enterprise deployment, mobile/desktop, plugins, and platform-specific material. We reviewed the official indexes, read the core architecture/session/security guides, and inspected relevant sections of the larger references. This is not a claim to have read every line of every product's documentation. The inventory below distinguishes focused reading from catalog review. Downloaded source material stays in ignored `.codine/research/`; this document contains original design decisions and short attributed summaries.

| Product | Primary sources reviewed | Coverage |
|---|---|---|
| Pi | [Index](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/index.md), [how it works](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/how-pi-works.md), [configuration](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/configuration.md), [security](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/security.md) | Full short guides and index |
| Pi | [Sessions](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/sessions.md), [skills](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/skills.md) | Core guides |
| Pi | [Compaction](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/compaction.md), [extensions](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/extensions.md), [JSON events](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/json.md), [models](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/models.md) | Selected mechanisms/reference sections |
| Claude Code | [Documentation index](https://code.claude.com/docs/llms.txt), [agent loop](https://code.claude.com/docs/en/how-claude-code-works), [best practices](https://code.claude.com/docs/en/best-practices), [permissions](https://code.claude.com/docs/en/permissions) | Index and relevant architecture/enforcement sections |
| Claude Code | [Memory](https://code.claude.com/docs/en/memory), [headless](https://code.claude.com/docs/en/headless), [checkpointing](https://code.claude.com/docs/en/checkpointing), [costs](https://code.claude.com/docs/en/costs) | Project guidance, automation, recovery boundaries, cost controls |
| Claude Code | [Hooks](https://code.claude.com/docs/en/hooks), [sandboxing](https://code.claude.com/docs/en/sandboxing), [skills](https://code.claude.com/docs/en/skills), [subagents](https://code.claude.com/docs/en/sub-agents) | Catalog/selected sections; not complete reference coverage |
| Codex | [Best practices](https://learn.chatgpt.com/guides/best-practices), [AGENTS.md](https://learn.chatgpt.com/docs/agent-configuration/agents-md), [security](https://learn.chatgpt.com/docs/agent-approvals-security), [sandboxing](https://learn.chatgpt.com/docs/sandboxing) | Current official manual retrieved using the documentation helper; relevant guidance/security sections |
| Codex | [CLI commands](https://learn.chatgpt.com/docs/developer-commands?surface=cli), [noninteractive mode](https://learn.chatgpt.com/docs/non-interactive-mode), [customization](https://learn.chatgpt.com/docs/customization/overview) | Relevant CLI, event, configuration sections; unrelated product surfaces excluded |
| OpenRouter | [Usage accounting](https://openrouter.ai/docs/cookbook/administration/usage-accounting), [reasoning preservation](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens) | Request/response compatibility, actual cost, complete reasoning metadata |

## What to learn from each agent

**Pi:** keep the core small. Its agent/session mechanisms support terminal, JSON, RPC, and SDK interfaces. Session history and model-facing context are separate; skills load detailed instructions on demand. Codine will retain one engine and add a structured output adapter rather than create a second automation engine. We keep our linear snapshots for now, rather than immediately adopting Pi's session tree. [Architecture](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/how-pi-works.md)

**Claude Code:** context gathering, actions, and verification form a repeated workflow. Documentation distinguishes model guidance from mechanically enforced rules. Codine will expose project conventions as instructions, enforce planning mode in dispatch, and use independent checks to grade results. A persuasive final response cannot substitute for a passing check. [Architecture](https://code.claude.com/docs/en/how-claude-code-works), [best practices](https://code.claude.com/docs/en/best-practices)

**Codex:** repository guidance, reproducible command-line workflows, and separation between approval policy and sandbox boundaries matter. Codine will add explicit project guidance and deterministic automation while continuing to label local command execution as unsandboxed. A directory constraint in a Python file tool does not constrain arbitrary subprocesses. [Guidance](https://learn.chatgpt.com/docs/agent-configuration/agents-md), [sandbox](https://learn.chatgpt.com/docs/sandboxing)

**Do not copy every feature immediately.** Plugins, MCP, background agents, hooks executing arbitrary commands, and complex routing all add behavior that must be evaluated. Our first bottleneck is measurement and reliable task execution, not feature count.

## Current state and gaps

Already implemented: streaming NVIDIA adapter, eight registered tools, exact approved edits, line/character pagination, session journal recovery, context trimming, checked restart, a terminal workbench, and 47 offline tests. Existing real-provider verification belongs to the previous release; it is not evidence that a new provider works today.

Missing foundations:

1. No enforced read-only planning mode.
2. No explicit project guidance loader.
3. Step count bounds loops but does not detect repeated unproductive actions.
4. Terminal events have no machine-readable CLI transport.
5. No task-level grader distinguishing model success from plumbing success.
6. NVIDIA-specific request settings prevent a clean OpenRouter switch.
7. Token counters are not a dollar budget and missing usage is not free usage.

## Implementation order and acceptance

### A. Enforced planning and project context

Add `--plan` and `/plan`. Planning advertises only file listing, reading, searching, and Python syntax checks. Dispatch must also refuse write/execute/restart calls, including hallucinated calls to hidden tools. `/permissions ask` exits planning. No model-callable tool can change this mode.

Load root `AGENTS.md`, then root `CODINE.md`, as bounded project guidance when assembling each request. State the order and show loaded paths with `/instructions`. Guidance is subordinate to user requests and harness policy. Do not execute imports/scripts or follow file references automatically. Reject symlinks and oversized instructions. Nested/global discovery is deferred explicitly; we must not pretend to implement Codex's full hierarchy.

Acceptance: planning produces no mutation even if a fake model requests one; instruction content is visible in request context, counted in the budget, and a changed file is reflected on the next turn.

### B. Run outcomes, repeated work, and event transport

Return a structured turn outcome: completed, interrupted, step_limit, or loop_limit. The CLI must not report success simply because the function returned. Detect a repeated identical call/result signature three times in a single user turn; finish/save the current batch, then stop before another model request. This is a conservative heuristic, not proof that a task can never benefit from retries.

Add `--json` for one-shot runs. stdout must contain only JSONL records; no banner, prompts, or colored diffs. Each record has a schema version, sequence number, event type, and data. Include a final run record so scripts can distinguish a finished answer from a stopped loop. Noninteractive confirmations remain denied. Preserve readable terminal mode through the same engine events.

Acceptance: JSON parsing succeeds for every stdout line; secrets are masked; an oversized/repeated run has a nonzero exit and an explicit outcome; valid tool-call/result pairs survive stopping.

### C. OpenRouter adapter and cost visibility, without inference

Select a provider explicitly through configuration/CLI. Keep NVIDIA as the existing default. OpenRouter uses its own endpoint and key; do not forward NVIDIA-specific template parameters.

Use complete non-streaming OpenRouter messages initially, preserving `reasoning_details` intact across tool turns. This avoids inventing a merge algorithm for provider-specific streamed reasoning blocks. The terminal announces the request and displays the complete answer when ready. NVIDIA continues to stream.

Attach a session-bound dollar ledger for OpenRouter. Before a request, reserve an explicitly configured maximum request charge from the local budget; settle against actual `usage.cost`. If transport fails or cost is absent, keep the reservation and block further requests until the operator reconciles usage. Reject invalid/negative/non-finite values. A local ledger cannot guarantee a remote billing cap; use an OpenRouter key credit limit as the provider-side boundary. A new session is a new local ledger, not a fresh account balance.

The initial ceiling can be $3.50 of the available $5, leaving $1.50 untouched. No model is chosen silently when OpenRouter is selected. Prices and endpoint capabilities must be checked before live use. Model selection will follow measurements, not a claim that one model is universally best.

Acceptance: mocked SDK calls verify endpoint/request shape, correct keys, reasoning round-trip, actual/unknown cost behavior, reservation persistence, and no network during tests. Paid calls remain off in this milestone.

### D. Offline task evaluations

Create 12 small Python coding tasks, eight development and four designated holdout. Each has a broken fixture, a task prompt, an expected behavior oracle, and a trusted reference solution used only for offline infrastructure checks.

Run the actual Agent against a scripted provider in a disposable workspace: read → exact edit → syntax check → answer. Grade executable behavior independently after the run. The model-visible workspace never contains the grader or reference solution. Execute both the broken fixture and reference solution to prove the grader rejects and accepts the intended cases. Treat timeouts and process errors as failures, not missing data.

The offline provider is an oracle replay, not a capable model. A 12/12 result means the engine and grader work together; it says nothing about live task solve rate. A task tagged holdout is only a convention in this local open repository, not a secret benchmark protected from the developer.

Save a report with task IDs, split, outcome, duration, grader result, and model calls. Reproducible offline reports are ignored local artifacts. Later paid runs must use isolated execution for model-written code; a temporary folder alone is not a sandbox.

### E. Final API stage (later)

Only after A–D pass: verify the current OpenRouter models/endpoints and a dedicated key limit, configure the local ledger, run a tiny compatibility smoke test, then a fixed-budget baseline. Compare one variable at a time. Repeat enough tasks to expose variability. Report cost per solved task, median/p95 elapsed time, tool failures, regressions, and unresolved tasks. Use actual provider costs; never count a missing cost field as zero.

Allocation remains $0.25 smoke, $0.75 baseline, $1.50 focused improvements, $1 final checks, $1.50 reserve. These are ceilings, not instructions to consume the balance. The present development run spends $0 on inference.

## Deferred capabilities and why

| Capability | Before implementing |
|---|---|
| Summarizing compaction | Establish baseline solve rate; measure summary cost and lost facts |
| Checkpoint/rewind | Track before/after identities and prove conflict-safe restoration; shell side effects need separate treatment |
| Nested instructions and skill discovery | Specify precedence, trust, budgets, and invalid-resource handling |
| Hooks/MCP/plugins | Establish restricted interfaces and lifecycle/error tests |
| Subagents/background execution | Prove cancellation, shared budget accounting, and conflict isolation |
| Automatic shell approvals | Implement a real sandbox and an explicit execution policy |
| Session trees/forks | Define migration and branch-specific context/accounting |
| Streaming OpenRouter reasoning | Add fixtures for provider-specific indexed reasoning fragments |
| Autonomous self-improvement | Isolated candidate checkout, immutable grader, external budget supervisor, and human-reviewed promotion |

## How to read the new code

`registry.py` is the enforcement point: the same allowed-tool rule controls schemas and dispatch. `instructions.py` supplies data for context, not executable permissions. `agent.py` owns the turn outcome and loop guard. `events.py` renders the engine's events for scripts. `budget.py` implements reservation/settlement arithmetic. The provider adapter performs transport only. `evals.py` uses the same Agent with a fake provider and grades code from outside the workspace.

A useful learning distinction: instructions influence model choices, dispatch restricts available actions, session storage records what happened, and a grader checks the result. Keeping these responsibilities separate makes bugs easier to locate.


## Implemented result for this milestone

A–D are implemented and verified offline: enforced planning, root project guidance, loop/outcome controls, JSONL mode, optional OpenRouter complete-message adapter, persisted session budgeting, and the 12-task scripted evaluation runner. There are 65 passing automated tests. Stage E is deliberately pending; no inference was run in this phase. See `../VERIFICATION.md` and `EVALUATIONS.md` for evidence and limitations.

### Follow-up: evaluation isolation

The single-file Python Docker executor is now implemented and tested; see `SANDBOX.md`. It has no host mounts/network and grades returned values outside the container. All 12 scripted exercises pass through it. This satisfies the execution-isolation foundation for those small tasks, not arbitrary repository builds. Multi-file staging and two package-level development exercises are now implemented (see the follow-up below). Paid inference is still deferred.

### Follow-up: multi-file staging

Implemented an explicit Python-only staging manifest and a Docker-only repository suite. Two development tasks exercise package imports and coordinated edits to two modules. Tests cover excluded files, symlinks, path validation, missing dependencies, and end-to-end scripted repairs. This remains infrastructure verification, not a model score. Next: broaden repository task diversity and add controlled failure/recovery scenarios before the final API stage. Dependency installation and arbitrary build commands remain deferred.
