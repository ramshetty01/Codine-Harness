# Milestone 6: external integration acceptance

This gate tests the supported local integration surfaces with fake providers and
real subprocesses. It does not spend API credits or claim universal compatibility
with every MCP transport, authentication service, or compatible model endpoint.

## End-to-end paths

`tests.test_sdk_rpc` starts the actual persistent RPC process. A client explicitly
trusts and connects a local stdio MCP fixture. Its scripted model discovers tool
metadata, requests activation of an exact tool revision, sees the full schema only
on the following request, and invokes the tool with parent approval. The result
returns through the ordinary Agent journal. The same suite checks shared-budget
settlement and unknown-charge blocking, cancellation, resumable sessions, and
refusal of approvals whose review output was truncated.

`tests.test_mcp` and `tests.test_mcp_modern` cover legacy initialization and modern
per-request metadata, bounded responses, cancellation, exact executable trust,
and failure handling. Modern input-required responses and server-initiated model
requests are unsupported and rejected. HTTP MCP is outside this implementation.

`tests.test_custom_provider` runs an actual loopback HTTP streaming server. The SDK
must explicitly accept unmetered operation and use the chosen endpoint; an explicit
refusal overrides trusted settings and sends nothing. Arbitrary compatible USD
costs remain unknown. Endpoint-bound capability declarations are required for
tools, redirects and environment proxies are disabled, and builtin provider keys
cannot be repurposed for custom endpoints.

`tests.test_auth` uses fake credentials and a local callback server. OpenRouter
PKCE login validates callback state, host and path, bounds the exchange, and saves
credentials privately. Malformed callbacks cannot abort a later valid callback.
The store is private plaintext, not encrypted or an OS keychain. No live OAuth
flow was exercised; Nvidia and custom endpoints support manual key entry.

`tests.test_cache_fallback` verifies observed cache counters, unknown coverage,
reviewed fresh-context fallback, exact custom endpoint retention, unchanged shared
receipts, and rejection when reviewed session state changes. Measurements use
injected completions, not live cache-hit or latency claims. Fallback never retries
an uncertain request automatically.

## Related boundaries

- Deferred tools: exact revision activation occurs only after a complete durable
  tool batch; hidden tools cannot be invoked by guessing their names.
- Orchestration: declined, failed and deferred actions stop dependent actions;
  attempt caps leave valid tool-call/result pairs.
- Result projections: bounded selection preserves provenance and truncation,
  without granting new read or write authority.
- Installed distribution: `scripts/check_install.py` checks the installed SDK,
  session resume and extension lifecycle outside the source checkout.

Run the full Docker-enabled suite and installed-wheel checks before accepting this
gate. Hosted CI must pass on the supported Python/platform matrix. Individual
feature tests establish the documented scope, not parity with every upstream
product feature.
