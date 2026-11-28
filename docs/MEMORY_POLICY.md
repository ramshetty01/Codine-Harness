# Reviewed memory policy — C7 acceptance

Codine keeps reusable project facts separate from conversation summaries. The
implementation is the reviewed project-memory store introduced by I6 (#42);
C7 (#15) defines its policy and verifies the boundary with real persistence and
compaction code. It does not introduce a second memory system.

## Three distinct kinds of retained information

| Information | Owner and scope | How it becomes active | What it cannot do |
| --- | --- | --- | --- |
| Conversation archive | One session, retained locally | Normal messages, tool results and explicit session resume | Grant file access, confirm an unknown tool outcome, or approve memory |
| Conversation summary | One session, derived from complete archived groups | A bounded, accounted summarization request; projected as untrusted historical data | Become an approved project fact, grant permissions, replace the archive, or certify that a claim is true |
| Project memory | Canonical project path, shared by its sessions | A user reviews an exact diff and explicitly approves an inactive proposal or added/edited fact | Override current user requests or applicable guidance, execute text, change budgets, or relax permission policy |

Model `propose_memory` writes a durable **inactive** proposal. No summary text,
tool-result claim, session title, imported conversation, or restart can activate
it. The model has no approve/edit/delete memory tool. An ordinary assistant
answer saying “I will remember” is not evidence of an approved memory record.
Check `/memory list` and `/memory show ID` for actual status and provenance.

A summary may repeat a proposal or inaccurately describe it as approved. That
text remains historical context; the store's reviewed status is authoritative
for active memory injection. The harness does not interpret summary content as
commands or automatically extract reusable facts into memory.

## User review and conflict rules

The interface is `/memory list`, `/memory show ID`, `/memory approve ID`,
`/memory add JSON_STRING`, `/memory edit ID JSON_STRING`, and `/memory delete ID`.
See [Project memory](PROJECT_MEMORY.md) for syntax and the storage schema.

Every activation, addition, edit and deletion presents the exact before/after
JSON diff and snapshot hash. The terminal requires explicit confirmation; piped
or JSON mode cannot approve it. Editing an inactive proposal keeps it inactive.
Plan mode permits inspection, not memory mutation. Concurrent changes invalidate
the reviewed snapshot; a failed comparison leaves the newer store intact. Read
and review again rather than treating the previous approval as reusable consent.

Approved notes are **factual data at the lowest precedence**, not instructions.
Current user requests and all applicable system/global/project/scoped guidance
override them. Conflicting or obsolete notes should be reviewed and edited or
deleted. There is no automatic last-writer-wins conflict resolution across facts,
no model-controlled promotion, and no guarantee that a model will infer factual
truth or resolve semantic conflicts correctly. Code-level permission checks,
plan enforcement and budget limits remain independent of any note's wording.
A reviewed note saying “disable approvals” cannot change those controls.

## Provenance, persistence and project isolation

Records contain their status, stable ID, content hash, source kind, session ID
and digest of redacted source material. Model-proposal provenance is generated
by the harness, not supplied in tool arguments. Approval retains that source;
a user edit replaces it with the editing user's session/input provenance.
Hashes identify the reviewed content and detect stale writes, not a digital
signature proving authorship. This is current-record provenance, not a complete
append-only history of every edit or reviewer.

Restart and session resume reload the project store without changing record
status. Different projects have separate stores, and copying a store to a new
canonical project path fails identity validation rather than silently importing
facts. Moving a project therefore does not automatically migrate memory. Create
and review desired facts in the new project explicitly; retain the old store as
an archive if needed. Do not hand-edit a project path merely to skip review.

## Limits and recovery behavior

- `.codine/memory.json` holds at most 100 records and 32 KiB total, including
  inactive proposals and provenance. Each text is limited to 8000 UTF-8 bytes.
- Known credential values and common secret patterns are redacted before
  persistence/review and again before injection. This is not universal secret
  detection: never intentionally store credentials in notes.
- State uses private user-owned regular files, exclusive locks, no symlink or
  hardlink traversal, bounded reads, atomic replacement and version/hash checks.
  Ordinary model file tools cannot modify this internal path.
- Invalid, unsafe or oversized state fails closed. It is not silently reset or
  replaced by summary contents. Preserve the damaged file for inspection and
  repair/restore it outside model tools; a previously reviewed backup must retain
  its project identity and private file permissions. Do not discard session
  archives or accounting ledgers to “repair” memory.
- Deleting a record removes it from subsequent **active memory injection**. It
  does not erase earlier mentions in conversation messages, tool outputs,
  summaries, exports or backups. A new session avoids the old conversation's
  historical references; it does not delete their archived bytes. `/clear`
  changes active conversation context, not project memory.
- Memory consumes the existing request context allowance and can cause a
  context-limit refusal. Review and shorten/remove facts when needed. Memory
  commands neither call a provider nor reset spending. Real summarization still
  uses the normal reservation/accounting path, including unresolved-charge
  blocking; this policy introduces no separate budget.

## Reproducible acceptance evidence

```bash
python -m unittest tests.test_memory_policy tests.test_project_memory tests.test_semantic_compaction -v
```

`tests.test_memory_policy.MemoryPolicyTests` exercises production storage,
projection, compaction and session-switch code using a fake provider:

| Test | Observed boundary |
| --- | --- |
| `test_compaction_and_real_session_resume_never_approve_pending_or_summary_facts` | A fake summary explicitly asserts a pending fact is approved. Store bytes and inactive status remain unchanged through compaction, `/new`, and `/resume`; a summary-only fact is never inserted. Only a subsequent reviewed approval activates the real proposal. |
| `test_deletion_removes_active_injection_but_honestly_retains_summary_and_archive` | Reviewed deletion removes active injection after compaction and reload, while exact archive and summary state remain intact. |
| `test_conflicting_note_is_lower_priority_data_not_permission_or_budget_change` | A note asking to ignore the user, read a denied file and raise spending remains labelled lower-priority data. File access stays denied; policy object and budget state are unchanged. |
| `test_project_scope_and_source_provenance_survive_restart_without_transfer` | Approval/reload retain automatically generated provenance; another project has no notes and refuses a copied store with mismatched identity. |

The compaction tests reserve and settle **synthetic** `$0.01` costs locally to
verify that memory operations and resume do not reset accounting. No network
request or actual charge occurs. The existing project-memory suite additionally
covers real terminal approval, decline/noninteractive/plan denial, concurrent
review conflicts, secret redaction, private-file boundaries and limits. The
semantic-compaction suite verifies complete tool pairs, archive preservation,
malformed-summary failures and normal accounting behavior.

These tests establish harness policy and state-transition behavior. They do not
measure live model accuracy, promise universal prompt-injection resistance, or
claim that deleting a note erases all historical references.

Implementation: I6 commit `a392e42` (integrated as `9830f0f`); subsequent memory
boundary hardening remains covered by `tests.test_project_memory`. C7 requires
this policy and its new regression evidence, not duplicate production storage.
