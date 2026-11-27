# Shared OpenRouter spending controls

Normal interactive OpenRouter sessions and bounded evaluations now use the same durable key-bound allowance at `STATE_ROOT/evals/budget.json`. Creating a new conversation, switching projects, importing a fresh conversation, or restarting the CLI does not create a new shared allowance. Per-session budgets remain a secondary bound and usage view.

The shared lifetime ceiling remains the existing evaluation allowance: at most **$3.50**. An existing lower limit is preserved. `CODINE_BUDGET_USD` continues to control the secondary per-session budget; it does not increase the shared ceiling. Interactive request reservations use `CODINE_REQUEST_BUDGET_USD` (normally $0.25); evaluation reservations remain $0.05. The local reservation is conservative accounting, not a remote provider price cap.

## Preserve existing accounting

Opening an old evaluation ledger upgrades it in place while holding the same exclusive lock used by evaluation processes. Its existing charged total becomes the receipt baseline, and its limit, key fingerprint, blocked reason and unknown pending amount are preserved. A legacy pending evaluation request receives a durable request ID with owner `evaluation`; an interactive session cannot settle it.

No API key is stored in the ledger. Its SHA-256 identity binds the allowance to the selected key. A different key fails closed and does not receive a fresh allowance automatically. Keep a backup when upgrading and do not run older Codine versions against the upgraded ledger: older writers do not understand owner receipts and will cause validation to fail closed.

This is a local allowance domain, not tamperproof account-global billing. `STATE_ROOT` is described in [Installation](INSTALLATION.md). In a checkout it normally belongs to that checkout; `CODINE_HOME` or installed user-state paths can select another domain. Moving/deleting/copying state is not a funding reset or a supported way to obtain more spending. The CLI retains the remote provider as the authoritative billing boundary.

## Before every interactive inference

The CLI configures the shared guard for all OpenRouter calls, including context-summary requests. The sequence is:

1. Acquire the exclusive ledger lock. Another process holding it causes a local refusal; no request is sent.
2. Recover any already-settled receipt into this session, without charging the shared total again.
3. Save the local intent, then persist a shared reservation with a UUID and an owner bound to this session ID and canonical project.
4. Read OpenRouter key metadata. Require at least `max($1.55, $1.50 + request reservation)` remaining; a normal $0.25 reservation therefore requires $1.75 available.
5. Persist a dispatch marker before invoking the completion adapter.
6. Settle the shared receipt using the reported actual cost, then mirror that cost into the session and save it.

The metadata read is a non-inference GET. A missing/invalid remaining balance, metadata failure, exhausted allowance or lock rejection prevents inference. A local reservation can settle to zero only when this code knows it has not invoked the provider. A completion HTTP error, timeout, disconnect or omitted cost never establishes zero charge.

The remote remaining-balance check protects the reserve across separate configured state roots, but it is not atomic with charges from other applications or installations. Set provider-side key limits as the final billing boundary. Costs exceeding a local request reservation are recorded and block further requests; they are not discarded to keep a score under budget.

## Unknown charges and reconciliation

After an uncertain request, every other session in the same allowance domain is blocked. `/budget` displays the shared total, outstanding request ID and owner, together with the current session's secondary budget. Verify the actual charge in provider activity, then reconcile from the original owning session:

```text
/budget reconcile REQUEST_ID ACTUAL_USD
```

The command checks both the saved session reference and durable owner. A copied/imported session with a new ID cannot clear the original session's pending charge. Repeating reconciliation with the same request ID and same amount is idempotent; a different amount is rejected. Successful receipts remain in the ledger and are never charged twice.

For an evaluation-owned pending request, use the explicit evaluation command:

```bash
python -m codine.live_evals --reconcile-request REQUEST_ID --reconcile-cost ACTUAL_USD
```

Both options are required together; this mode sends no inference. Evaluation reports now include `pending_request`. Historical session-only unknown charges from versions without shared request IDs are not guessed or silently converted to zero: review their original session/accounting state before continuing. Migrated evaluation pending charges are supported through the evaluation owner route above.

## Crash recovery

The shared ledger has a durable identity and unpruned request receipts. If a process dies after shared settlement but before saving the session, reopening the original session copies the settled cost into its still-pending local reservation once. If the same ledger contains a saved local intent but no shared reservation, or a reservation never reached its dispatch marker, the original owner can recover it as proven non-dispatch. A different ledger identity is rejected rather than treating missing history as proof of no charge.

Files are written atomically and fsynced; the exclusive lock covers dispatch and settlement. Ledger/lock symlinks, hardlinks, nonregular files, wrong ownership and shared-write permissions are rejected. Receipt history is bounded at 10,000 requests and fails closed when full. Review/archive design is a future operation; deleting history to reset a cap is not supported.

## Embedding and tests

The terminal CLI calls `Agent.configure_shared_budget(path, read_metadata=...)` automatically for OpenRouter. Scripted offline agents and the evaluation adapter retain explicit injected accounting; evaluation already guards its transport and must not be charged by two shared wrappers. An application embedding `Agent` with a paid provider must configure the shared guard itself.

Tests use fake keys, temporary ledgers and injected metadata callbacks—never the user's ledger or API. Coverage includes preserving a legacy charged/pending fixture, multiple sessions, fresh/import-style sessions against an exhausted allowance, owner mismatch, idempotent settlement, local-save crash recovery, metadata rejection before dispatch, and a real spawned-process lock/unknown-reservation test.
