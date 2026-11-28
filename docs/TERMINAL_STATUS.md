# Working status without hidden requests

The interactive footer separates four kinds of information:

1. Permission mode and executor first, followed by the model when space permits.
2. Current phase and input queue: `Q` is total queued input, `D` is its deferred
   subset, and `C` is commands waiting for the main prompt. Deferred input needs
   explicit `/next`; it is still visible while idle. `/queue` reviews its text.
3. `ctx est INPUT+OUTPUT/LIMIT`: the cached byte-based input estimate, reserved
   output, and configured local context limit for the **last prepared** request.
4. Local session cost: reported spending and any unsettled reservation or block.

`/status` prints the full snapshot without footer clipping, including queue lane
counts, ready/deferred counts, and explanations. It works in noninteractive runs
without inference. At narrow widths the footer clips each row independently;
permission mode stays first and context/cost retain their own rows. Use `/status`
for details that do not fit.

## What the phase means

`preparing`, `accounting`, `provider`, `processing`, and `tool running` reflect
actual agent phases. `provider` includes waiting for a response and streaming;
it does not imply the provider has already produced tokens. `REVIEW APPROVAL`
means a permission review is underway. `STOP REQUESTED` means cancellation was
requested during work, not that a process has already stopped. `finishing input`
means the busy reader is being handed back to the main prompt. `idle` means no
active turn/request; it is not a claim that the user's task is complete.

## Context is not usage

The agent computes the context snapshot on the execution thread, from its actual
prepared messages and schemas. The footer only reads that cached snapshot. It is
not a provider tokenizer, cumulative session token usage, or an exact prediction
of the next request. Later tool results, newly typed input, context changes and
streamed output are not automatically added. A different model or session hides
the old snapshot until another request is prepared. `/context` explicitly
inspects the next projection; `/usage` reports provider-returned token counts.

## Cost and concurrency limits

The footer never reads a shared budget ledger, obtains its lock, or contacts the
provider. Cost comes only from the in-memory local session ledger. Missing cost
is `unknown`, and `$0` is shown only when the local ledger explicitly contains
zero. A pending reservation is labeled **unsettled**; the reserved amount is not
reported as an actual charge. `/budget` checks the shared allowance and handles
reconciliation. These display changes cannot release reservations or permit a
request.

Queue counts use a nonblocking in-memory lock. If a queue update owns it, the
footer uses its last snapshot (marked `~`), or unknown counts if none exists.
It never waits for the queue's disk write or holds a lock while an input reader
joins. Recovered queues are loaded by the main thread before prompting. The UI
thread does no workspace reads, instruction loading, token estimation, approval
requests, or network work. Periodic refresh is provided by the terminal editor;
state changes also become visible on ordinary prompt redraws.
