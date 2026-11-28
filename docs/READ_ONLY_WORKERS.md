# Read-only workers (P1 library)

This module supplies the worker for P2 supervision. It does not register a delegation tool or launch a second terminal reader. Runtime access must pass through the supervisor; the worker library alone does not complete the P1 milestone.

A worker gets a fresh conversation, its explicit redacted task, and freshly loaded policy-checked instruction guidance. It does not copy parent chat, read coverage, task plans, executable extension/MCP handlers, skills, jobs or credentials into its prompt. It constructs no provider and sets no environment variables. Its private runtime configuration uses a nonsecret accounting placeholder. The supervisor owns the real provider and credentials.

Only `list_files`, `read_file`, `inspect_file`, `search`, `navigate_symbols` and `read_notebook` exist in the worker registry. Permission is checked both by exact name and read effect. The child uses a fresh project-bound Workspace in planning mode with the parent's read policy, protected paths and approval callback. Read-ASK still requires approval, read-DENY still blocks, and credentials/links stay excluded. There are no write, command, restart, memory-proposal, executable integration, dynamic tool-loading or recursive delegation tools. This is application-level read restriction, not a sandbox for arbitrary Python code.

Parent edit permission is unchanged by child reads. The parent must read current file contents itself before editing. Returned source hashes describe bytes observed at that time, not proof that the current file is unchanged. The result contains at most 16 source metadata records with bounded paths and page/metadata scope. Search-only results may not have a source hash; none is invented. The summary is model-produced **untrusted evidence**, not instructions or verified success. Every result reports `verified: false`.

## Worker interface

`ReadOnlyWorker(parent_workspace, config, session_store, *, complete, redactor, cancel_event, parent_link, limits=None, emit=...)` saves a separate child Session during construction and exposes `.session`. The supervisor must durably record the parent run first and provide `parent_link = {role: child, parent: SESSION_ID, run: RUN_UUID}`. It then durably attaches the child ID before permitting the first model callback.

`.run(task)` is single-use. It returns bounded evidence on normal completion; exceptions and cancellation propagate without retry. `.result()` permits the supervisor to inspect partial status/evidence after a failure. `.close()` releases only the supplied child store. Child archives cannot be resumed as independent ordinary sessions.

The completion callback implements `(messages, schemas, emit)` plus `.model`, `.max_output_tokens`, and a deep-copy `.billing_snapshot` property. It must call the parent's existing `Agent.complete` accounting boundary. The worker never reserves, settles, reconciles or creates an independent shared allowance. Its saved billing is an audit snapshot of the parent; its usage is observational and must not be added again to the parent's already-counted usage. Unknown charge on cancellation remains pending under the parent owner. A child failure does not justify retrying the request.

## Bounds and cancellation

Defaults are six model steps, 1,024 output tokens per request, 12,000 cumulative token allowance units, 60 seconds, an 8 KiB task and an 8 KiB returned summary. The supervisor may choose larger explicitly bounded limits, including matching the parent's configured output reservation; it cannot silently reduce a provider cap by changing only the child prompt. Worker construction rejects a callback whose declared actual output limit exceeds the selected limit.

The cumulative allowance conservatively charges the existing input/schema token **estimate** plus reserved output for every attempt. If reported usage exceeds that reservation, the larger count is retained. This is not an exact tokenizer or a retrospective guarantee of provider compliance. A large schema or history can exhaust it before the step count. Requests are blocked before dispatch when their estimated reservation exceeds the remaining allowance. No money is reserved by this token accounting.

Cancellation/deadlines are checked before requests, during emitted stream events, and around each read tool. A cancellation flag is never cleared by the worker. Its checks cannot forcibly interrupt arbitrary blocking Python or a stalled provider transport: P2 owns transport interruption and lifecycle supervision. The child agent's existing journal repair preserves complete tool/result pairs after interruption; interrupted read results can be marked unknown and are never replayed automatically. Summary truncation and source-list truncation are explicitly reported.

Offline tests use a fake provider behind the **real** parent Agent/shared-ledger boundary: they check one charge per request, parent ownership, no allowance reset, pending unknown charges, context isolation, blocked tools/paths, policy copying, absent parent edit coverage, bounded summaries and archival child sessions. No live provider requests are used.
