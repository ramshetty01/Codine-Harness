# Codine practical release — 27 November 2026

Codine is now a usable Python terminal harness with measured live tool use, file recovery, a model picker, and bounded evaluation spending. It is inspired by Pi's small extensible core, not a claim of matching Pi's complete product.

## Start and work

Run `codine` inside the project you want to edit. This installation's local `.env` selects OpenRouter and `qwen/qwen3.7-flash`, the model used for the successful baseline. The public `.env.example` retains NVIDIA as the original default; users must supply their own key and explicitly select OpenRouter and a model.

1. Ask Codine to inspect a small part of the repository and explain the change.
2. Review the proposed diff. Ordinary edits require `yes` unless you explicitly enable `/permissions auto-edit`.
3. Ask it to run the relevant test. Interactive `run_command` is still an approved **host command**, not a Docker command.
4. Use `/changes` to see file-tool checkpoints, `/diff` to inspect the most recent file, and `/undo` to review restoring its before-image.
5. Exit and use `codine --continue` to resume. Use `/restart` after editing Codine's own implementation.

`/model` opens the current OpenRouter tool-model catalog; `/model free` narrows it to models with zero advertised prompt/completion/request/reasoning prices. The selector shows advertised input/output prices per million tokens and context size. Provider quotas, endpoint availability, and tiered pricing still apply. Escape cancels. A model switch preserves the session budget.

## What changed, and why

### Reliable edits when quoting is difficult

`edit_file` still requires an exact, unique substring from a previously read region. It does not normalize punctuation or guess what the model intended.

A repeated live task exposed a model supplying a Unicode minus sign where the source used ASCII `-`, together with incorrectly escaped strings. Exact editing correctly refused the changes. Repeated attempts eventually exhausted the local context limit.

The new `replace_lines` tool offers a different address for an edit: path, inclusive line range, replacement text, and the SHA256 returned by `read_file`. It verifies that the current file has the same hash, that the entire selected region was read, and that no concurrent edit happened during approval. It also rejects partial reads of long lines and accidental joining of untouched lines. The model used this tool successfully in the follow-up. This is an additional operation, not weaker matching.

`search` now accepts either a directory or a single file, matching the way the live model tried to use it.

### Recoverable file changes

Before a CLI file-tool write, Codine saves a private record in `.codine/changes/`. The record contains a before-image and the expected hash after the write. If saving this record fails, the write does not proceed.

`/undo` checks that the file still matches the expected after-image, shows a reverse diff, asks for approval, and rechecks before replacing/removing it. A newly created file can be removed only if it is unchanged. A changed file is refused rather than overwritten. Undo is disabled in planning mode and is not a model-callable tool. Successful undo adds a conversation notice and clears read tracking so the model must inspect the restored state.

These are single-file checkpoints, not multi-file transactions or Git commits. They do not undo shell commands, network effects, database changes, or arbitrary user edits. A crash after saving a checkpoint but before writing can leave a prepared record; the hash comparison determines whether it can be restored. Checkpoints are local private data and may contain source text. They are ignored by Git and are not automatically deleted. Use Git for long-term history.

### Live measurement separated from development

The offline runner still cannot construct a live provider. The new `codine.live_evals` module is an explicit entry point for paid tests. It creates disposable task workspaces, supplies only the task prompt and tool interfaces to the model, removes host command/restart tools, and supplies a Docker-backed `run_checks` tool. That tool exists only in evaluations.

The model never receives reference patches. The host compares returned values with fixed expected answers. A task counts as passed only when the agent completes and the independent grader passes. A good patch followed by a provider error is retained as an incomplete run.

Reports are stored in ignored `.codine/evals/`. New reports include model, image ID, selected tasks, requests, charged costs, latencies, tool failures, candidate files, and conversation traces. Keep traces private. The early baseline function report predates trace capture; its aggregate outcomes and request timing remain recorded.

### Spending that persists across tasks

`EvaluationBudget` locks one ledger for the entire evaluation invocation. Every task uses that same ledger, so starting a new task or another process does not reset the $3.50 allowance. Concurrent evaluation invocations are refused. A changed key does not silently reset it either.

Each request reserves $0.05 before sending. Requests are bounded by 30 KB of serialized input, 2,048 output tokens, 12 steps per task, and provider price filters of $1/M input and $2/M output with zero per-request fee. A run has an additional explicit ceiling of at most $0.75. A metadata check before each request requires at least $1.55 left on the key, preserving the intended $1.50 reserve under the reservation policy.

Actual `usage.cost` settles the ledger. Unknown charges retain the reservation and block another request. After checking provider billing, explicitly reconcile an uncertain request with:

```bash
python -m codine.live_evals --reconcile-cost ACTUAL_USD
```

This is separate from the interactive session's `/budget` ledger. It does not alter provider-side key limits. Local reservations and advertised prices are not a guarantee about remote billing; retain the provider key's credit cap and monitor it. The price-routing semantics come from [OpenRouter provider routing](https://openrouter.ai/docs/guides/routing/provider-selection), and key usage/remaining fields from [OpenRouter key metadata](https://openrouter.ai/docs/api/api-reference/api-keys/get-current-key).

## Evaluation history, including failures

- Initial `qwen/qwen3-coder-next` smoke: code passed the grader, but the fourth request returned HTTP 429 before a final answer. Counted as incomplete. Confirmed successful-call cost: $0.00051772. After delayed provider usage matched successful charges on subsequent reads, the rejected request was reconciled at $0. Evidence is kept locally.
- `qwen/qwen3.7-flash` smoke: 1/1 completed, $0.000172142.
- First fixed baseline: 12/12 small function tasks plus 2/2 multi-file package tasks completed. Combined cost $0.003095458, median task time about 13.9 seconds, nearest-rank p95 27.245 seconds. Two intermediate tool/check failures were retained; both tasks recovered.
- Repeat before line editing: median passed; extension failed after mismatched edits and context exhaustion. The run stopped before frequency. Cost $0.000717938. This failure is not removed from the record.
- Targeted follow-up with `replace_lines`: extension and frequency both completed, used the new tool, and recorded zero tool failures. Cost $0.000562652. A single successful follow-up is encouraging, not statistical proof of causality.

Final verification totals and total spending are recorded in `../VERIFICATION.md` after the final regression run.

## Where to learn from the code

| Module | Responsibility |
|---|---|
| `agent.py` | Repeated model → tools → results loop and durable action boundaries |
| `registry.py` | Tool schemas and dispatch-time permission enforcement |
| `workspace.py` | Read tracking, exact edits, hash-guarded line edits, diffs and undo checks |
| `changes.py` | Durable local before-images; no model or UI behavior |
| `sessions.py` | Conversation snapshots and interrupted tool-result recovery |
| `context.py` | Whole-turn context projection without deleting the archive |
| `router_catalog.py`, `model_picker.py` | Metadata filtering and explicit selection |
| `eval_budget.py` | Locked cross-task spending state |
| `live_evals.py` | Controlled experiments using the real agent |
| `sandbox.py` | Restricted Docker execution, independent of interactive host commands |

The model proposes actions. Python enforces permissions, executes tools, saves history, and checks results. That separation is the central concept of the harness.

## What is still outside this release

This is not Pi feature parity. There are no session trees, mid-request steering, background agents, general extension/skill packages, MCP, semantic summarization, arbitrary sandboxed repository builds, or dependency installation in the evaluator. Context still stops safely when one active turn exceeds its limit. Benchmarks are small, public, standard-library Python tasks; successful runs do not establish large-repository reliability, security expertise, or superiority over another agent.

The next useful expansion is representative tasks from your own repositories, with a frozen test set and reviewable diffs. Add features when those tasks show they are needed.

### Second regression finding: duplicated reasoning context

The first full regression after adding line edits completed nine function tasks, then stopped on extension. Its final candidate passed the behavior grader, but the active context exceeded the harness limit before a final response, so the run remains incomplete. Cost was $0.002395792; the subsequent repository run passed both tasks for $0.000699654.

Inspection showed the same reasoning text stored both as a top-level `reasoning` string and inside `reasoning_details`. `context.request_message` now omits the top-level string **only** when every structured block is textual and their concatenated text exactly matches it. Structured blocks remain unchanged, encrypted/different blocks are untouched, and the archive retains the original message. Context accounting also counts the system message once. This saves request space without widening the spending allowance or discarding tool-call/result pairs.

Task timings include API latency and Docker execution on the local machine. Runs were performed during development, sometimes alongside local checks; they are not controlled throughput benchmarks.
