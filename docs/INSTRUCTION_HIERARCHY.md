# Instruction hierarchy and scope

Codine reads instruction documents as bounded text, never executable policy.
The precedence is explicit:

1. System/harness rules and direct user requests.
2. User-global guidance from `~/.config/codine/AGENTS.md`.
3. Project-root `AGENTS.md`, refined by root `CODINE.md`.
4. Ancestor-directory instructions for selected file tasks. Deeper directories
   refine project conventions only inside their own subtree; `CODINE.md` refines
   `AGENTS.md` at the same directory. They cannot override user-global guidance.

The user-global path is captured from the invoking user's home before loading any
project dotenv. It does not use the checkout's `CONFIG_ROOT`, project settings or
an environment variable loaded from a repository. The global file is optional and
is not created automatically. Edit it outside Codine's model file tools: the exact
path, case/Unicode spelling variants and existing samefile aliases are protected,
even when Codine is launched against the user's entire home directory. Descriptor
reads reject symlink components and hard links, including for this global file.

## How a subdirectory becomes active

Codine always considers user-global and project-root documents. It does not scan
the repository for every `AGENTS.md` or follow references inside documents.
Nested documents become eligible only along ancestor directories of:

- Explicit attached files (`@src/module.py`).
- File-tool paths, including notebook cells, grouped edits, and both move endpoints.
- Directory scopes explicitly supplied to list/search/symbol navigation.
- Declared command/check working directories and explicit staged `files` lists.

A host command's arbitrary argv strings are not interpreted as file selections.
For example, `cwd="."` with `argv=["python", "src/run.py"]` does not automatically
activate `src` guidance. Read/select relevant files first, use an appropriate cwd,
or inspect `/instructions src/run.py`. Read/search results do not activate every
returned filename. New files use their intended parent-directory ancestry.

Each loaded document has JSON provenance: source, SHA256, user/project kind and
explicit scope such as `src/**/*`. Sibling instructions remain scoped to their own
subtrees even when a turn works on several directories. A document cannot claim
higher priority or a larger scope for itself. Normal model requests and `/context`
use the same projection. Semantic compaction includes the current instruction
context as **data for the summarizer**, not replacement summary instructions.

## Newly discovered or changed guidance

Before each model step, Codine refreshes the active documents and records which
versions are included in that request. Before each model-requested tool action it checks relevant
scopes and document versions again. Read tools can discover a scope. A mutation,
command execution or restart that encounters unseen or changed guidance is recorded
as **skipped, not executed**. The next model request receives the refreshed scoped
context and can reconsider the action. This includes a write following a read in
the same model-generated batch: reading a file does not mean the model already saw
its newly discovered instructions.

Every requested tool still gets a matching result; skipped actions are not counted
as executed repetitions. Existing step, billing, context and cancellation limits
remain active. Refreshing guidance does not authorize an edit, waive approval,
change command access, expand filesystem access, select a model or alter budgets.
Nested instruction files themselves require core-file approval to edit, including
in auto-edit mode. Previously completed side effects are not undone by a later
instruction change.

Scopes reset for a new turn, session switch/import and fresh process. A resumed
conversation keeps its archive, but current document versions are loaded when
relevant paths are selected again; old summaries and tool outputs are not a new
source of instruction authority.

## Inspection and permissions

`/instructions` displays global/root guidance and the currently selected scopes.
`/instructions PATH` previews the hierarchy for one file/directory without changing
the active task's scopes. `/context` lists the exact automatic sources, scopes and
hashes used in the next request projection. Instruction inspection is not an edit
read and does not populate file-version or read-coverage authorization caches.

Automatic project-document loading skips read ASK and DENY rules; it never opens
an approval prompt implicitly. Explicit `/instructions` inspection honors ASK and
DENY. A one-time inspection approval is not a permanent rule change: later automatic
requests still skip ASK documents. User-global guidance has its own fixed trusted
reader and is not read through model-visible project tools.

Each document is at most 16 KB; `AGENTS.md` plus `CODINE.md` at one directory must
also fit 16 KB. Combined instruction content is limited to 64 KB, 32 documents,
16 active nested directories and 12 ancestor levels. Oversize, invalid UTF-8,
binary, linked or changing documents fail closed. Narrow the task or shorten the
documents rather than relying on silent truncation.

These are explicit provenance and permission boundaries, not a claim of model
immunity to prompt injection. Instruction text remains lower-priority guidance;
file/command permissions are enforced independently by the harness. Shell commands
still have the access granted through their configured executor and approval.

## Offline acceptance coverage

Tests cover precedence/provenance, selected ancestors versus siblings, refreshed
root documents, unseen nested-write deferral, read/write batches, attachments,
notebook and move endpoints, declared cwd versus argv, global-file alias protection,
nested core approvals, policy ASK/DENY, bounds, symlinked user configuration,
context inspection and summary-data projection. Providers are controlled fakes;
these checks do not spend inference credits.

User-global guidance is accepted only when the opened file belongs to the invoking
user and is not group/other writable (`0644` is allowed). Its descriptor-walked
ancestor directories must belong to that user or root and reject shared writes;
root-owned sticky temporary ancestors are allowed, but the final containing
directory must not be shared-writable. These checks run on opened descriptors,
including a second file check after reading, so a path-only permission precheck
cannot grant trusted provenance to a swapped file. Project documents remain
lower-priority conventions and do not gain user-global provenance.
