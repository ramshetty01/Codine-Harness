# Extension API v1 implementation contract

This is the version-one SDK and runtime contract for X1/X2/X3.
See EXTENSION_COMMANDS.md for the terminal workflow and EXTENSION_WORKERS.md for supervision limits.

## Authority and loading

Use the I3 `snapshot_extension(workspace, manifest_path)` result and
`ExtensionTrustStore.trusted(snapshot)`. The snapshot binds project directory
identity, manifest identity, exact declared file bytes and capabilities. Never
import a repository file during discovery or trust review. A user first reviews
and grants the exact snapshot through `/trust`; `/extensions load MANIFEST` then
loads only that trusted snapshot. Stage the returned bytes in a private temporary
directory and never reread source files into a running worker.

Capabilities are `tools`, `commands`, `events`, and `ui`. These are declared API
surfaces, not operating-system permissions. Trusted Python executes with the
user's host privileges. It is not a sandbox. File tools, budgets and model request
ownership remain in the parent. There is no extension API to approve trust, alter
billing, fabricate tool results or replace core commands.

Check the authoritative receipt before future calls. Revocation stops future
execution; it cannot undo prior shell, filesystem or network effects. Changed
resources require a newly reviewed digest. A running worker identifies the
snapshot it loaded. Reload is explicit and occurs at a completed input/tool
boundary; old registrations are removed only after a replacement validates.

## Python interface

An entry point exports `register(api)`. The trusted SDK exposes API version 1 and
bounded registrations:

```python
def register(api):
    api.tool("describe", "Describe a value", {
        "type": "object", "properties": {"value": {"type": "string"}},
        "required": ["value"], "additionalProperties": False,
    }, lambda args: {"description": args["value"]})
    api.command("hello", "Show a greeting", lambda text: "Hello " + text)
    api.on("turn_end", lambda event: None)
    api.panel("status", lambda: "Extension ready")
```

Names are validated and scoped to the extension. Tools use an `ext_` namespace;
commands use `/ext NAME:COMMAND ARGUMENTS`. Core names cannot be replaced. Registry
schemas and callable handlers must agree. Unknown versions, malformed schemas,
duplicate names, undeclared capabilities and excessive registrations fail closed.
Tool handlers run only after parent-side approval and are disabled in planning
mode because arbitrary Python cannot be certified read-only from its declaration.
An explicit user extension command is a separate invocation surface, never a
model-controlled slash command.

UI contributions are sanitized plain text, not arbitrary terminal escape codes or
PromptSession objects. Events contain bounded documented data snapshots; they
cannot mutate the agent's in-memory session. Initial event support is `turn_end`
after a successful durable run completion. Paused, failed, cancelled and restart turns do not invoke it.
Callback failures disable that worker and report a warning without changing the saved turn outcome.
Events are best-effort notifications, not durable exactly-once jobs: a crash after saving but before
notification can omit an event, and resume never replays it. Event payloads are the version-one
run record (outcome, reason, timestamps, plan revision, verification and billing state) plus
session ID, with no conversation text, credentials or mutable Agent object.

## Worker protocol and supervision

Launch a fresh isolated Python interpreter (`-I`) using a trusted worker module.
The initial import path includes the staged declared package and installed dependencies.
Trusted Python can change import paths or read host files; this is not an import sandbox. Environment forwarding
is minimal and excludes provider secrets. This is hygiene, not protection against
trusted code reading host files.

Use bounded JSONL requests/responses with protocol version and request IDs.
Redirect ordinary extension print output to capped stderr; malformed protocol
output disables the worker. Registration and invocation have deadlines. Drain
stdout/stderr concurrently, cap output, check cancellation and terminate the whole
process group on timeout/unload/exit. Registration failure must leave the core
registry unchanged. Worker failure must not replay a model/tool action or claim
that side effects were undone. Retain a useful sanitized error result.

## Acceptance evidence

- An untrusted manifest is inspected without executing its top-level Python.
- An explicitly trusted fixture registers a tool, command, event and plain panel.
- A model-selected extension tool requires parent approval; planning mode refuses.
- Namespace conflicts and incompatible versions cannot replace core behavior.
- A crash, infinite loop, oversized output and broken JSON disable only the worker.
- Exit/cancel/unload terminate children and do not leak descriptors or secrets.
- Changed source requires fresh trust before reload; existing workers retain their approved
  staged snapshot. Revoked receipts block future calls. Reload is explicit.
- A saved session resumes without replaying extension actions or automatically
  granting trust to a relocated project.
- SDK examples and installed-wheel tests work outside the source checkout.

All initial verification uses local fixtures and scripted providers. No paid
inference is necessary to establish this contract.

## Runtime limits and lifecycle

At most eight extensions and 64 extension tools can be active in one process.
Each registration permits up to 32 handlers and 32 KB of metadata. Tool names are
`ext_<normalized-name-prefix>_<8-character-name-hash>_<local-tool>`; collisions fail
without modifying existing registrations. Commands and panels use `NAME:LOCAL`.

All extension execution, including initial registration, commands and panels, is
disabled in plan mode. Each model-selected extension tool requires explicit parent
approval, including in auto-edit mode. Explicit user command/panel invocations
and completion event hooks use the already granted snapshot trust. These hooks
can have host effects; no declaration makes arbitrary Python read-only.

Load/reload occurs only at the idle CLI boundary. A failed replacement leaves the
old registry and worker intact. A handler failure disables the worker without
retry; unload removes its declarations and reload needs a valid current receipt.
Session switches within the process retain explicitly loaded extensions but do not
replay hooks. Process exit/restart unloads everything; resumed processes must load
again explicitly. No extension handler runs merely because it appears in saved
messages. Restart validation failures before process replacement keep extensions
active; failure after unloading requires explicit reload.

Run the offline acceptance suite:

```bash
python -m unittest tests.test_extension_api tests.test_extension_trust tests.test_extension_process tests.test_extension_manager tests.test_extension_commands tests.test_extension_lifecycle -v
```
