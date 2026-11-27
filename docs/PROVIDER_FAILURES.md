# Provider failures and retry safety

Codine separates a failure that might be temporary from a request that is safe to replay. Those are different questions: a timeout can be temporary while the remote inference has already completed and incurred a charge.

## Completion requests

Both completion adapters keep the SDK's `max_retries=0`. Codine does not automatically resend a completion after HTTP 429, 5xx, timeout, disconnect or incomplete streaming. It does not infer zero billing from an HTTP status, exception class, missing response or provider error message.

The OpenRouter ledger reserves a request budget before dispatch. If the call fails without settled usage/cost, that reservation remains pending. A second completion is blocked. Inspect provider activity and use `/budget reconcile AMOUNT` with the actual charge before deliberately continuing; do not use zero merely to clear the block.

The CLI classifies SDK failures and supplies sanitized advice:

| Category | Typical evidence | Next action |
| --- | --- | --- |
| Authentication | HTTP 401 | Check the provider key and shell-versus-`.env` precedence |
| Permission | HTTP 403 | Check model/account permissions |
| Not found | HTTP 404 | Check endpoint and exact model ID |
| Context/output limit | Recognized machine-readable limit code | Inspect `/context` and token reservations |
| Request size | HTTP 413 | Reduce context or output size |
| Quota/billing limit | HTTP 402 or recognized quota code | Inspect account limits/balance |
| Rate limit | HTTP 429 | Wait and inspect provider limits/request size |
| Provider unavailable | HTTP 408/409/425 or 5xx | Check availability and wait |
| Timeout/disconnect | SDK timeout/connection exception | Check connectivity and whether the request completed remotely |
| TLS configuration | Certificate validation error | Fix certificates; do not disable validation |
| Other rejection/failure | Remaining classified status or error | Inspect configuration and provider activity |

No raw response body, exception message, authorization header or provider request URL is copied into classification metadata. `Retry-After` can be displayed as an advisory, capped at one day. It does not establish permission to replay a completion. Existing local/stream-format errors still report their bounded local messages.

**A5 remains partial:** the current adapters do not expose validated zero-charge/replay evidence or a trusted pre-dispatch retry contract. Automatic inference retry is therefore deliberately unimplemented. Closing that gap requires a provider-specific proof path, budget reconciliation semantics, and tests covering lost/partial responses. Metadata retries below are not inference retries.

## Read-only metadata requests

The OpenRouter `models`, `key`, and `credits` endpoints are read-only GETs and do not run inference. Their actual request path uses bounded retry scheduling:

- At most three attempts by default.
- A five-second socket/read budget for each attempt.
- A twenty-second scheduling budget across attempts.
- Exponential delays starting at 0.5 seconds, with bounded jitter and a two-second delay cap.
- Valid numeric or HTTP-date `Retry-After` hints are honored only when they fit the delay/deadline budget. A longer hint stops retries; it is not silently shortened into an early request.
- Authentication, permission, quota and malformed/schema/oversized responses do not retry. Cancellation propagates immediately.
- HTTP error responses are closed. Successful response bodies are read in chunks with an 8 MB cap and an elapsed-time check between reads.

The scheduling deadline is not a hard thread kill: one blocking socket read can finish after the elapsed deadline, up to its socket timeout. No further attempt is then scheduled outside the remaining budget. This is bounded synchronous network handling, not a guarantee of an exact wall-clock return time.

Metadata errors return a generic unavailable message without credentials. Parsing a successful metadata response happens inside the attempt; invalid JSON or a missing `data` field is not treated as a transient network error.

## Verification

`tests/test_failure_policy.py` constructs actual SDK authentication, rate-limit, context, timeout and connection exceptions. Sleep, monotonic clock, wall clock and jitter are injected into retry tests. Tests exercise the real metadata GET path with fake responses, verify adapter SDK retries remain disabled, and verify persisted pending reservations block replay for SDK 429/timeout/disconnect errors. No provider network requests or model calls are needed.
