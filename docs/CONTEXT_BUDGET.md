# Context budgets and output reservation

Before every model request, Codine projects complete recent user turns out of the
saved conversation. The archive remains intact. The system instructions, project
instructions, tools, tool results, and provider reasoning fields all consume
request space. Only an exact duplicate of textual reasoning is omitted; opaque
reasoning is preserved unmodified.

Two independent boundaries apply:

- `CODINE_CONTEXT_CHARS` (default 160000) limits serialized message and tool schema
  characters. It remains a separate protection against large payloads.
- `CODINE_CONTEXT_TOKENS` (default 65536) is a **local policy ceiling**, not a claim
  about any model's context window. Input estimate plus reserved output must fit.
  `CODINE_MAX_TOKENS` (default 4096) is the requested output reservation.

The estimate counts UTF-8 bytes of serialized messages and schemas, plus 64 tokens
and 16 per message for framing headroom. This deliberately overestimates ordinary
prose; it is not a tokenizer and does not guarantee acceptance by a provider.
Unicode, JSON metadata, encrypted reasoning and tool arguments count. The status
line explicitly says “estimate”; reported provider usage remains actual usage.

Configure exact model IDs with capacities obtained from your provider:

```dotenv
CODINE_CONTEXT_TOKENS=65536
CODINE_MAX_TOKENS=4096
CODINE_MODEL_LIMITS='{"your-provider/model-id":{"context_tokens":32768,"output_tokens":2048}}'
```

These example numbers are illustrative, not published capabilities of a model.
Both fields are optional in each model entry, but an entry must contain at least
one. Unknown fields, booleans, nonpositive values and noninteger counts are rejected.
No model catalog or inference request is needed to read these settings.

For that model the effective window is `min(65536, 32768)` and the output cap is
`min(4096, 2048)`. Codine therefore leaves 2048 tokens for generation and estimates
input against the remaining 30720. Both NVIDIA and OpenRouter send that same
2048 cap. Reasoning may consume part of a provider's output allowance too.

The active session model is looked up on every request, so `/model` switches and
resumed sessions use their own configured limits. An unknown model uses the local
ceiling and configured output cap; Codine does not invent provider capabilities.
Set limits for each model you use, especially models with small windows.

If a request exceeds either boundary, Codine drops the oldest complete user turn
and tries again, counting the added omission notice too. It never cuts through a
tool-call/result group. If even the current turn cannot fit, it stops before
billing reservation or provider invocation. Use `/clear`, a smaller task, a smaller
output allowance, or verified larger limits. Output reservation equal to or larger
than the effective window is rejected rather than silently sending an invalid
request. Summarization and model-specific tokenizers are not implemented here.

Offline regression coverage: `tests/test_context_budget.py` exercises Unicode,
opaque reasoning, tool-pair preservation, exact budget boundaries, independent
character limits, model/output selection, configuration validation, and rejection
before provider execution. Existing reasoning/archive tests remain in place.

`/context` inspects the same projection used by the request loop, without a model
call or archive mutation. It shows loaded instruction paths, active/omitted
message counts, tool schema/result sizes, estimated input, reserved output and
remaining local capacity. It explicitly reports that no semantic summary exists
and marks oversized current turns as over-limit. The user's next message is not
included yet. Figures are conservative estimates, not exact provider token counts.
