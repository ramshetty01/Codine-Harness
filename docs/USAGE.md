# Provider usage

Use `/usage` to inspect the most recent completed provider response and the sum of
reported input, output, total, cache-read and reasoning token counts. Each field
shows its coverage: `2/3 responses` means one response did not supply a valid count.
A measured zero is shown as zero; an omitted, negative or malformed count is
unknown. Codine does not reconstruct missing counts from message lengths.

Cache-read counts come from `prompt_tokens_details.cached_tokens`; reasoning
counts come from `completion_tokens_details.reasoning_tokens`. These are subsets
of input and output respectively, not additional tokens to add to the total.
Provider adapters that omit either field leave it unknown. This reports usage,
not a claim that caching is enabled or that a particular model supports reasoning.

Detailed counts persist across restart/resume. Older sessions retain their legacy
counters but begin detailed coverage only when a new response is recorded. A fresh
transcript import starts fresh usage accounting; project migration retains the
session's historical counters. Compaction requests use the same accounting path.

A response that returns usage can still leave a billing problem: token counts do
not prove the dollar cost. `/budget` remains the authority for reservations,
known charges and reconciliation. Interrupted responses with no complete return
are not assigned inferred token counts. Unknown charges still block further paid
requests under the existing budget rules. No provider request is needed to run
`/usage`.
