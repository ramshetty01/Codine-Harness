# Controlled external calls and JSON result projections

Codine executes each model-requested tool separately through its existing durable
journal. There is no nested batch executor, parallel paid-call scheduler or
automatic replay. Every known result is saved before the next action starts.
MCP/extension tools retain their trust, schema activation, plan-mode, cancellation
and per-call approval checks.

## External call limits

External means a deferred integration tool registered by MCP or the extension
manager. The default limits are **four attempts per assistant tool batch and
twelve per user turn**. Attempts include denied approvals and rejected/error calls;
unstarted skipped actions do not consume an attempt. Core file/read/filter tools
are not external calls. The terminal reports the active limits when dispatching
an external batch. Embedders may set `agent.external_limits = ExternalLimits(...)`
with 1–16 attempts per batch and a turn limit between that batch limit and 64.
Tool arguments and project instructions cannot change these limits.

After an external result reports an explicit top-level error, status
`declined`/`failed`/`error`/`blocked`/`skipped`/`deferred`, explicit
`action_executed: false`, or an MCP `isError: true`, all remaining
**actions** in that batch are saved as skipped, including dependent core writes.
This includes explicit instruction-policy refusals before an external handler
starts. A limit check also precedes core actions that follow the final permitted
external attempt. Codine does not guess failure from arbitrary prose. A subsequent model step may
inspect the results and replan within the remaining turn allowance. Reaching the
batch cap also skips remaining actions; reaching the turn cap ends the turn with
`tool_limit`, state `paused`, `retryable: false`. The user must inspect results and
explicitly continue. This does not automatically drain queued follow-ups.

Provider requests retain existing token/cost reservations and unknown-charge
blocks. This feature adds no provider call or budget authority. Host-privileged
extensions and MCP servers can independently call paid services; those charges
are not measured or capped by Codine's model ledger. Count limits are not dollar
cost guarantees. Existing server trust warnings continue to apply.

## Project existing results rather than repeating calls

The read-only `project_tool_result` tool accepts exactly one source:

- `tool_call_id`: an unambiguous saved result or a known result already journaled
  in the current batch. Repeated historical IDs fail with bounded candidate
  message indices rather than silently selecting a different turn.
- `message_index`: the exact zero-based archive index of a saved `role: tool`
  message. It cannot point to a user/system message.
- `artifact_id`: an immutable local artifact whose content hash is verified.

Select JSON data with `pointer_path` (empty for the root) using RFC6901 `/` paths
and `~0`/`~1` escapes. For arrays, `offset` and `limit` select at most 50 rows. Use
`fields` for up to 16 JSON Pointers relative to each selected row/value. Values
remain JSON booleans, numbers, strings, lists or objects; nothing is evaluated.
`decode_json: true` explicitly parses a selected JSON-encoded text string once,
which is useful for MCP tools that return JSON arrays inside text content.

Example:

```json
{
  "artifact_id": "<hash returned by the original tool result>",
  "pointer_path": "/rows",
  "fields": ["/id", "/status"],
  "offset": 0,
  "limit": 10
}
```

Responses include typed selected values, source tool/call/index/artifact
provenance, a hash of the historical source content, array totals, next offset and
truncation indicators. `source_complete` describes the stored snapshot, not the
completeness of a remote service's dataset; upstream completeness remains unknown.
An offloaded history message is explicitly marked incomplete and points to its
full artifact if available. Artifact reads do not reread current files or refresh
file read/version permissions. Context compaction and later filesystem edits do
not turn these historical values into current verification evidence.

Limits are 2 MB of source JSON, depth 32, 20,000 parsed nodes, 512-character
pointers and 8 KiB of output. Duplicate keys, nonfinite numbers, invalid JSON,
ambiguous IDs and unsupported selectors are rejected. If the selected result is
too large, narrow fields or rows; the source remains intact. There are no regexes,
shell commands, arbitrary expressions, imports, URL fetches or automatic resource
expansion. Artifact lock contention fails promptly rather than hanging the agent.
Known secrets pass through the existing redaction boundary before model output.

Offline regression coverage includes multi-source results offloaded to artifacts
and projected without repeating calls, preserved tool pairs after cap/failure,
known-result cancellation recovery, malformed JSON, pointer escaping, repeated
IDs, read-authority separation, artifact integrity and busy locks. No provider or
external paid API is needed for these tests.
