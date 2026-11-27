# OpenRouter streaming

OpenRouter now uses the existing OpenAI-compatible SDK with `stream=True` and
SDK retries disabled. The SDK parses SSE frames and ignores keepalive comments.
Codine's adapter reconstructs one assistant message; the agent still owns tool
execution, persistence, redaction, and spending authorization.

## What becomes visible, and when

Text reaches the UI as it arrives. Reasoning text is displayed only when the
existing reasoning setting is enabled. Encrypted reasoning is never displayed.
Tool fragments stay inside the adapter until the entire iterator finishes and
its terminal marker and complete arguments validate. Thus seeing a partial
answer does not mean the turn succeeded or its requested tools ran.

Tool calls are accumulated by their integer index, including interleaved calls.
Names and JSON arguments are concatenated; IDs must be stable, nonempty and
unique. The final indices must be contiguous, arguments must be JSON objects,
and the finish reason must be `tool_calls`. Truncation, invalid indices, missing
IDs, unsupported/error finish reasons, conflicting terminal markers, and content
after completion fail closed. Local limits bound frames, bytes, reasoning blocks,
and tool count. The adapter supports one text/function choice, not multimodal
output or parallel alternative completions.

## Reasoning is provider state

`reasoning_details` is preserved in order and returned in the assistant message
for the next request. Encrypted/unknown blocks are copied without interpretation,
reordering, deduplication, or decryption. Adjacent compatible unsigned text or
summary deltas are joined. Metadata conflicts begin a new block, and a signature
closes its text block. Index alone does not identify a reasoning block. This
follows the official OpenRouter adapter's adjacent-block/signature convention;
new provider-specific formats still need representative fixtures before support
can be claimed. Plain `reasoning`, `reasoning_content`, and refusal text are also
retained. The UI avoids repeating normalized reasoning and its detail in the
same frame.

## Completion and spending are separate

The final accounting chunk can have `choices: []` or repeat the finish reason
with an empty assistant delta. Both are accepted without printing another answer.
Final usage is a snapshot, never a sum of chunks. Preliminary usage is not a
substitute for missing final usage or cost. OpenRouter automatically supplies
usage; the deprecated `include_usage` option is not required.

The SDK consumes `[DONE]` internally. Codine therefore validates the exposed
terminal finish marker and iterator completion; it cannot independently prove
that a wire-level `[DONE]` arrived. Missing final usage/cost remains unknown even
if the text looks complete. The shared spending guard retains the dispatched
reservation and blocks tool execution and further paid requests until explicit
reconciliation. Reasoning-only responses ending in `length` are still responses
that can incur cost, not free failures. Cancellation, decoding errors, stream
errors, and disconnects are never automatically retried or settled to zero.
The stream context manager closes resources on success and failure.

## Evidence and limits

`tests/test_openrouter_stream.py` uses real SDK parsing with an in-memory HTTP
transport and split SSE bytes, including keepalives, Unicode, interleaved tools,
reasoning round trips, final accounting, errors, and cancellation. It also checks
that an unknown cost keeps a temporary shared ledger reservation. These are
infrastructure fixtures, not paid endpoint tests or model capability claims.

Protocol references:

- [OpenRouter streaming](https://github.com/OpenRouterTeam/docs/blob/main/api_reference/streaming.mdx)
- [Reasoning tokens and details](https://github.com/OpenRouterTeam/docs/blob/main/guides/best-practices/reasoning-tokens.mdx)
- [Official reasoning reconstruction implementation](https://github.com/OpenRouterTeam/ai-sdk-provider/blob/main/src/chat/index.ts)
- [Usage accounting](https://github.com/OpenRouterTeam/docs/blob/main/cookbook/administration/usage-accounting.mdx)
