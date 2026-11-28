# Reviewed project memory (I6)

Codine can retain short project facts across sessions and process restarts. It is
not a permission system or an executable extension. Notes enter each new model
request as explicitly labelled, lowest-precedence untrusted data. Current user
requests and all applicable instructions override them. Verify facts when they
matter: user approval does not establish factual correctness.

## Propose, inspect, approve

The model has one memory tool, `propose_memory(text)`. It records an **inactive**
proposal. It cannot approve, edit or delete memory. A proposal is not included in
active memory context, even after restarting or switching sessions. Proposals are
project-wide, so a different session in the same project can review them.

Use these terminal commands:

```text
/memory list
/memory show RECORD_ID
/memory approve RECORD_ID
/memory add "This project runs unit tests with unittest."
/memory edit RECORD_ID "This project runs unit tests with python -m unittest."
/memory delete RECORD_ID
```

`add` and `edit` take one JSON string, not JSON objects or shell expressions.
`list` displays status, ID, content hash and source session. `show` includes the
full text and provenance. `approve`, `add`, `edit` and `delete` display an exact
before/after JSON diff plus a snapshot hash, then require the interactive
`Approve? Type yes:` confirmation. Noninteractive input cannot approve changes.
A rejected review leaves memory untouched. Editing an inactive proposal leaves
it inactive; approve it separately. Editing an approved record requires review
and keeps it active. Delete also removes inactive proposals to free space.

The review is optimistic: the store is not locked while you read the diff. When
you approve, Codine locks the store and checks the entire revision and snapshot
hash. A concurrent proposal or edit makes the old review stale and refuses the
write. Review the new state before retrying. Plan mode permits inspection only;
it blocks model proposals and all user memory mutations.

## Scope and provenance

The private store is `.codine/memory.json`, version 1, tied to the canonical project
path. Copying it to another project does not silently import it. Session switching
and restart do not change the project scope. Each record contains:

- A stable random ID, `proposed` or `approved` status, text and SHA256 of the text.
- Source kind (`model_proposal` or `user_input`) and source session ID.
- A SHA256 digest of the redacted source message or explicit user input.

The model cannot choose provenance fields. Edits replace source provenance with
the reviewing user's session and input digest. This is current-record provenance,
not an append-only audit log; original/edit history is not retained in this store.
The SHA256 is an integrity/version identifier, not a cryptographic signature
proving the user's identity. A person with host filesystem access can edit files
outside Codine's safeguards.

Approved notes are loaded afresh during request preparation, including requests
after tool results. Deletion removes a note from subsequent **memory injection**.
It does not erase existing conversation messages, saved transcripts, or summaries
that may have mentioned the fact. Start a new session if you also need to avoid
historical references. Memory consumes the existing context budget and may make
a request too large; it never increases context or spending limits. No memory
command calls a provider or resets an accounting ledger.

## Storage boundaries

The aggregate store, including inactive proposals and metadata, is limited to
32 KiB and 100 records. Individual text is at most 8000 UTF-8 bytes and excludes
terminal controls. Known secret values and common credential patterns are
redacted **before** storing or reviewing text, and again before context injection.
This is not a guarantee of detecting every possible secret; do not store secrets
in memory. If a new secret becomes known later, displaying/contextualizing old
notes redacts it, but old stored bytes are not automatically rewritten.

Normal model file tools cannot read or write `.codine`. Only the dedicated memory
path updates this state. Files and locks must be private user-owned regular files
with one hard link; symlinks, shared permissions and oversized/corrupt state fail
closed. Descriptor-based directory walks avoid following symlinks. Writes use a
private temporary file, fsync and atomic replacement under an exclusive lock.
The feature does not execute note text, interpret `@` attachments or slash
commands, or let a note change policy, providers, credentials or budgets.

## Offline evidence

`python -m unittest tests.test_project_memory -v` covers inactive proposal restart,
redaction and generated provenance, exact approval diffs, decline/noninteractive/
plan denial, stale concurrent reviews, edit/delete changes to next-request
context, project identity, private state exclusions, link/mode/schema boundaries,
and aggregate size/count limits. Its real PTY test uses a fake provider: a model
proposes a fact, the user approves it at the actual terminal prompt, the next
request sees it, the user deletes it, and the following request omits it from
memory context. These tests perform no live inference.

Memory loading checks the opened descriptor before and after reading. Changes to
contents, ownership, permissions or link count during the read fail closed; retry
with a fresh snapshot. A loaded snapshot is data, not a permission policy, and
these checks do not claim immunity to model prompt injection.
