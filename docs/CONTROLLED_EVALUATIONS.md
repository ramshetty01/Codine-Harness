# Retained attempts and controlled comparisons

Every offline, command-corpus and live evaluation invocation now records a unique
attempt ID before task execution. Default paths include that ID. An explicit
`--report PATH` must be new: existing evidence is never overwritten by another
invocation. Progress updates replace only that attempt's own report and refuse
external modifications. A crash can leave a `running` partial report; it remains
evidence, not a discarded run. Ordinary caught aborts persist `aborted` status.
These files are local measurements, not cryptographically authenticated receipts.

For example, collect two offline baseline attempts, change the harness, and
collect two candidate attempts:

```sh
python -m codine.evals --offline --report /tmp/before1.json
python -m codine.evals --offline --report /tmp/before2.json
# Make the intended harness change, preserving task and grading conditions.
python -m codine.evals --offline --report /tmp/after1.json
python -m codine.evals --offline --report /tmp/after2.json
python -m codine.compare_evals \
  --baseline /tmp/before1.json /tmp/before2.json \
  --candidate /tmp/after1.json /tmp/after2.json
```

Offline scripted/reference tasks test the harness and grader, **not model solve
rate**. Real same-model repetitions belong to the final budgeted evaluation
phase. This work makes no live inference calls and does not reset allowances.
The existing live evaluator keeps its key reserve, run cap, shared ledger,
unknown-charge stop and Docker-only execution controls.

## What must match

Controlled comparisons require version-2 provenance, at least two and at most
20 independently identified attempts per condition, and identical task source,
graded checks, provider/model, request configuration, resolved Docker image and
recorded runtime conditions. Nested source/configuration/control fingerprints are
checked. Harness source may differ between conditions but must stay fixed within
each condition. Grading functions and executor implementations are separately
fingerprinted: changes there invalidate the comparison instead of appearing as
agent improvements. Source fixtures include public and held-back checks by hash;
reports do not reveal hidden answers through provenance.

Live evaluation freezes local capability declarations/catalog adaptation for the
invocation and records effective request settings without tool schemas or
credentials. The ordinary actual provider path uses that same frozen profile.
Tool schema/prompt changes remain harness changes; sampling/routing/output caps
are comparison controls. Legacy reports remain readable but lack sufficient
controls for this stricter comparison.

Local fingerprints do not attest upstream model weights, server load or the
physical endpoint behind a gateway. Same-model results can still vary. Resolved
image IDs and recorded Python/dependency/platform conditions are compared, but
unobserved host load is not claimed controlled. Results are descriptive, not
statistical proof or a claim of parity with another coding agent.

## Metrics and missing data

All retained attempts participate, including failures and partial/aborted runs.
Unattempted selected tasks stay in the success-rate denominator and are listed
separately from attempted failures. A copied attempt ID cannot count as a repeat.
The comparison reports pass-count range/standard deviation, task-latency
variation, per-task pass-rate changes, regressions and improvements. Comparable run latencies
sum measured tasks only for finalized full task sets; partial-run totals remain
unknown with explicit coverage. Their measured task subtotals are retained, and
incomplete task latency is not invented. Attempt envelopes
also retain elapsed wall time when finalized.

Each run reports tool failures, interventions and incorrect edits with explicit
coverage. A failing task does **not** prove an incorrect edit. Incorrect-edit
counts remain unknown unless a dedicated observation provides them. Live task
runs record zero human interventions because this automated evaluator never asks
for human input; this does not claim unseen external activity was measured.
Aborted in-flight task/request counts are marked incomplete.

Known charged cost is a subtotal. Missing cost stays unknown, and pending or
blocked accounting prevents a complete-cost claim or cost-per-solved metric.
Known spend is retained even when another request is pending. No zero charge is
inferred from a failure. Counter fields omitted by old reports remain unknown;
regressions require the controlled repeated comparison, not one lucky run.

Comparison inputs are capped at 20 MB each and 64 MB total. Output is a new
retained comparison artifact; existing input or output paths are never replaced.
