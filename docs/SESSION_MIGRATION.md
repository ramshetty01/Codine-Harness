# Sessions after moving or renaming a project

Sessions bind to their canonical project path. If a project directory is moved,
ordinary `--resume` refuses the old workspace identity. Do not edit the JSON's
project field or delete billing state to bypass that check.

Codine provides an **offline, explicit migration**. Move/copy the project including
its `.codine/sessions` directory using your normal filesystem workflow, then:

```sh
codine --project "/new/project" --migrate-session SESSION_ID --from-project "/original/project"
```

This previews the archived source ID/path/hash, selected destination, message count,
model/provider, retained spent amount, and deterministic new session ID. It prints
a confirmation command. Review the paths, then run that command:

```sh
codine --project "/new/project" --migrate-session SESSION_ID --from-project "/original/project" --confirm-migration SHA256_FROM_PREVIEW
```

The confirmation binds to the exact archived bytes and destination. Changed source
bytes require another preview. The original archive is preserved; the result is
a fresh session under `migrated-...`. Repeating the same confirmed migration returns
the existing ID instead of overwriting or duplicating it. Resume with the printed
`codine --project ... --resume ...` command.

Use the exact original absolute project identity stored in the archive. An old
compatibility symlink is not followed to redefine this identity. The destination
is resolved canonically. The original directory need not exist and is never
automatically created or edited.

## What is preserved and what is reset

Conversation/tool-result history, usage, summary metadata, historical plan details,
provider/model, saved executor/image choice, and per-session spent/limit data are
retained. Current Codine system instructions replace the archived system message.
A migration notice marks old absolute paths and command results as historical;
paths embedded in arbitrary text are never rewritten by guessing.

The new session has a fresh owner identity and migration provenance. Read/edit
permissions, Git baseline, last-run state, delivered-inbox receipts and settled
shared-request references do not transfer. In-progress plan entries become pending;
completed plan assertions remain historical, not fresh verification. User command
policies and access profiles must be explicitly supplied again when launching.
Credentials and provider authentication are never copied by this operation.

The shared spending ledger is neither modified nor reset. Migration cannot clear
another owner's unknown charge. A different state-root domain is not an account
credit reset: preserve the installation's existing shared ledger and limits as
described in [Shared spending](SHARED_SPENDING.md). In a moved checkout, its existing
`.codine/evals/budget.json` should move with the checkout. The migration command
does not relocate state directories for you.

## Refused states and recovery

Pending tool journals, uncertain/running action state, pending/blocked local
billing, and any nonempty/malformed inbox are refused. Migration never repairs
such states by replaying tools or declaring an unknown request free. Preserve the
archive and, where possible, temporarily restore the original directory path to
resume/reconcile there first; do not overwrite an unrelated directory occupying
that path. Shared pending requests owned by other sessions still block subsequent
inference through the normal global guard.

The source session lock is held throughout validation/save; an active source lock
refuses migration. Files must be regular single-link bounded JSON, not symlinks or
hardlinks. Duplicate JSON fields and incompatible schemas fail closed. A new
destination lock guards publication. Source history remains available if saving
the new snapshot fails. Source snapshot limit is 20 MB; inbox inspection is 600 KB.
Preview may create private lock files but does not write a session snapshot.

`--json` emits structured preview/result output. This command runs before
configuration/provider initialization, consumes no model tokens, and is suitable
for offline recovery. It does not transfer pending queues, running processes,
filesystem checkpoints, or external side effects into a different project.
