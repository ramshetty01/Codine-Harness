# Codine evaluation guide

## Two different questions

**Does the harness work?** Offline scripted runs test context assembly, tool dispatch, edits, syntax checking, persistence, and grading. Their responses are supplied by trusted fixtures.

**Does a model solve tasks well through this harness?** Only live, independently graded runs answer that. A scripted 12/12 is not a model benchmark and must not be reported as one.

The offline runner needs no API key and makes no inference calls. A separate, explicitly invoked live runner is now available after the user authorized bounded OpenRouter evaluations on 27 November 2026.

## Run the local checks

```bash
.venv/bin/python main.py --check
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m codine.evals --offline
```

The default report is `.codine/evals/offline.json`, ignored by Git. Select a split with `--split development` or `--split holdout`; choose a report path with `--report PATH` (an existing file at that path is overwritten).

The runner deliberately has no `--live` switch. It never constructs a live provider. Ordinary `main.py` still supports live providers when you send a normal task; `--plan` restricts tools, not billing.

## Fixture tasks

| ID | Split | Behavior checked |
|---|---|---|
| positive_sum | Development | Ignore nonpositive values |
| slug | Development | Whitespace normalization and lowercase |
| dedupe | Development | Preserve first occurrence order |
| median | Development | Odd/even lengths and empty input |
| flatten | Development | One-level flattening and empty groups |
| chunks | Development | Retain short trailing chunks |
| factorial | Development | Correct inclusive multiplication and zero |
| palindrome | Development | Case/punctuation normalization |
| safe_divide | Holdout | Zero denominator and signed division |
| extension | Holdout | Dotfiles, missing extension, final suffix |
| clamp | Holdout | Values inside/outside inclusive bounds |
| frequency | Holdout | Case-insensitive counts |

These are deliberately small first tasks. They do not yet measure repository-scale reasoning, complex dependency changes, or security expertise. Expand tasks only after the baseline runner is trustworthy.

## How a run works

1. The runner creates a disposable project containing only broken `task.py`.
2. An external grader executes behavior cases against the broken fixture; it must fail at least one.
3. The actual Agent uses the scripted provider to read, replace, and syntax-check the file.
4. File edits are permitted only in this disposable test setup. Command/restart approvals are denied.
5. The external grader runs again; every case must pass.
6. Saved history reloads and is validated for complete tool pairs.
7. A report records the task ID/split, stop outcome, grading result, runtime, and scripted call count.

Reference solutions and the grader live outside the workspace. The scripted provider knows the reference by design; it is an oracle for exercising plumbing. A holdout label in a developer-readable repository is a procedural convention, not a secret dataset. Freeze a later live evaluation set before tuning, and avoid inspecting its expected patches while iterating on development tasks.

The default local grader uses isolated Python import configuration and a timeout, **not an OS sandbox**, and is for trusted fixtures only. The Docker backend now adds a restricted container boundary: use `--executor docker`. Expected answers stay on the host, while source and inputs go to the container. See [SANDBOX.md](SANDBOX.md) for verified controls and limitations. Live inference remains disabled in this runner.

## Independent grading and truthful results

A final assistant message saying “done” does not pass a task. A run must finish normally and satisfy external behavior checks. A step-limit, repeated-action stop, execution error, or timeout is a failure. The broken-fixture check prevents an accidentally vacuous grader from producing a false perfect score.

Keep full diagnostics locally. Report aggregate success counts separately from provider connectivity failures. Record model ID, serving endpoint, harness commit, task version, request parameters, actual cost, elapsed time, and number of tool calls for future live runs. Do not silently rerun failures until they pass; report retries as attempts and charge them to the budget.

## Paid-stage procedure

Before spending the $5:

1. Freeze the current code/tasks and verify all local tests.
2. Use the tested Docker executor; extend and verify it before evaluating multi-file projects or dependency installation.
3. Verify current OpenRouter model/endpoint support and price. Select a model explicitly.
4. Set a dedicated OpenRouter key credit limit. Configure the local session ledger; recognize that it is not an account-wide cap.
5. Run a tiny read-only compatibility request first, including usage/cost validation.
6. Run the development split with a fixed step/output/time allowance.
7. Compare one improvement at a time with the same task conditions.
8. Run the held-out split after tuning, repeat selected cases to measure variability, and preserve failed attempts.

Suggested ceilings: $0.25 smoke, $0.75 baseline, $1.50 focused improvements, $1.00 final checks, $1.50 unspent reserve. Stop early when the evidence is sufficient. Model routing variations and billed failures must be reflected in actual costs.

## Budget state machine

```text
ready → reserve & save → send one request → actual cost → settle & save
                              │
                              └─ unknown cost / interruption → keep reservation
                                                             → block next request
                                                             → operator reconciles
```

`/budget reconcile AMOUNT` settles the one uncertain request using its actual provider charge. It is a user command, not a model tool. A request whose actual charge exceeds the reservation blocks further requests. There is no automatic unlimited retry, budget-reset tool, or assumption that missing usage means free inference.

## Future improvements, measured in order

Start with task completion and boundary compliance. Then measure cost per successful task, unnecessary tool calls, repeated failures, and latency. Only then investigate semantic compaction, richer skills, alternative models, parallel agents, and self-improvement loops. A faster or cheaper answer that fails the task is not an improvement.

## Live runner

```bash
python -m codine.live_evals --model qwen/qwen3.7-flash --task positive_sum --run-budget 0.25 --report .codine/evals/my-smoke.json
python -m codine.live_evals --model qwen/qwen3.7-flash --suite repository --run-budget 0.25 --report .codine/evals/my-repo.json
```

These commands spend real OpenRouter credits. The model ID above was evaluated on 27 November 2026, not promised to remain available. Candidate execution requires Docker; there is no host fallback. Host command and restart tools are removed from the live registry. A `run_checks` tool runs behavior checks in fresh containers. The model sees returned values and pass/fail, not reference solutions or expected-answer files. The final grader uses the same fixed cases; this is an openly inspectable small task suite, not a secret benchmark.

The shared ledger is `.codine/evals/budget.json` under the harness checkout. Its $3.50 ceiling persists across invocations; a concurrent run or changed key cannot silently reset it. Per-run caps are $0.05–$0.75, and each request reserves $0.05. The runner queries key availability before each request to retain a $1.50 reserve. Missing charges block calls until the operator checks provider billing and explicitly runs `python -m codine.live_evals --reconcile-cost AMOUNT`. Do not delete the ledger to get around a limit.

Reports retain failed attempts. They include task outcomes, returned values, actual known costs, pending reservations, timings, tool errors, and (in the current format) candidate files and conversation traces. A run may stop before all selected tasks; compare `selected_tasks` with the result rows. Traces stay local and ignored by Git. The interactive `/budget` is session-scoped and distinct from this evaluation ledger.

[The practical release guide](PRACTICAL_RELEASE.md) records the evaluated models, observed failures, targeted changes, and interpretation limits.
