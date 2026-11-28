# Milestone 3: long-task context and recovery gate

Run this deterministic, offline pipeline acceptance test:

```sh
python -m unittest tests.test_long_task_gate -v
```

It uses the real Agent, context projection/automatic semantic compaction,
SessionStore, durable tool journal, local budget and shared reservation/receipt
ledger. All projects, state and fake-cost ledgers live in a temporary directory.
The provider and its billing metadata are scripted; no network request occurs.

The single task preserves `greet(name)`, corrects its greeting, and adds no
dependencies. Its initial independent Python subprocess check fails. Six bounded
investigation results grow the conversation enough to trigger **at least two
actual automatic compaction requests**, not manually inserted summaries. Every
summary request must still contain the original requirement. Summaries are
scripted facts; observed hashes, failed exit status and `passed: false` are kept
by the production evidence extractor.

A three-action tool batch then saves a known checkpoint result, writes the actual
Python file, and raises KeyboardInterrupt inside that write handler before its
result is returned. The third action must not run. Recovery preserves the known
checkpoint and explicitly records unknown outcomes for the remaining calls; it
does not infer that the interrupted write succeeded from the script's intent.

The test closes the SessionStore and constructs a fresh SessionStore and Agent.
Reload makes zero provider calls and executes zero tools. Exact archive bytes as
represented by saved messages, failed evidence, original file hash and cost totals
survive. Only an explicit continuation request starts work. The provider must
see unknown-outcome evidence before requesting a real isolated Python check of
the current file. Its successful result is required before the scripted final
answer. An independent final assertion checks the actual file behavior, and the
side-effect log proves neither the interrupted write nor the unstarted action
was replayed by recovery. Original archive messages remain unchanged prefixes
of the resumed archive, with valid tool-call/result pairs and no pending journal.

Every summary and ordinary request reports a synthetic $0.01 cost. The gate
counts them separately and asserts that their sum equals both the final local
spend and shared spend. Every request has a settled shared receipt, including
compaction requests before interruption; resume creates no new allowance or
forgets prior charges. This money is test data, not paid inference.

Related boundary coverage remains useful:

- `tests.test_semantic_compaction`: malformed/truncated summaries, oversized
  groups, summary-save faults and unknown summary charges fail closed.
- `tests.test_fault_injection`: uncertain transport charges, partial streaming,
  durable known-result recovery and publication faults.
- `tests.test_tool_orchestration`: declined, failed or deferred external actions
  stop dependent same-batch actions; call limits preserve valid journals.

This gate establishes pipeline retention and recovery for scripted facts. It
**does not measure live summarizer fidelity, long-task model solve rate, automatic
retry idempotence, or cross-process crash durability**. A fresh model may explicitly
request another action after inspecting uncertainty; recovery itself never replays
it. Live summary-quality and capability evaluation remain the separately budgeted
Milestone 1 evaluation work. Do not label this offline gate as model performance.
