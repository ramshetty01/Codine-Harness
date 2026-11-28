# Extension API v1 implementation contract

This is the implementation contract for X1, with X2/X3 integration gates tracked
separately. It describes work in progress, not a shipped extension interface.

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
after durable run completion. X2 must establish ordering, timeout/failure behavior
and cancellation with tests before expanding automatic hooks.

## Worker protocol and supervision

Launch a fresh isolated Python interpreter (`-I`) using a trusted worker module.
The worker can import only the staged declared package through the explicitly
installed import path, plus normal installed dependencies. Environment forwarding
is minimal and excludes provider secrets. This is hygiene, not protection against
trusted code reading host files.

Use bounded JSONL requests/responses with protocol version and request IDs.
Redirect ordinary extension print output to capped stderr; malformed protocol
output disables the worker. Registration and invocation have deadlines. Drain
stdout/stderr concurrently, cap output, check cancellation and terminate the whole
process group on timeout/unload/exit. Registration failure must leave the core
registry unchanged. Worker failure must not replay a model/tool action or claim
that side effects were undone. Retain a useful sanitized error result.

## Acceptance evidence to implement

- An untrusted manifest is inspected without executing its top-level Python.
- An explicitly trusted fixture registers a tool, command, event and plain panel.
- A model-selected extension tool requires parent approval; planning mode refuses.
- Namespace conflicts and incompatible versions cannot replace core behavior.
- A crash, infinite loop, oversized output and broken JSON disable only the worker.
- Exit/cancel/unload terminate children and do not leak descriptors or secrets.
- Changed source and revoked receipts block future calls; reload is explicit.
- A saved session resumes without replaying extension actions or automatically
  granting trust to a relocated project.
- SDK examples and installed-wheel tests work outside the source checkout.

All initial verification uses local fixtures and scripted providers. No paid
inference is necessary to establish this contract.
