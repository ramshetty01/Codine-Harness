# Discovering and managing extensions

Extension commands run at the idle terminal prompt. Commands submitted while a
model turn is busy use the existing submitted-command queue; they do not mutate
tool registrations halfway through that turn. There is no model-callable load or
reload tool and no startup auto-discovery or auto-load.

```text
/extensions discover
/extensions discover extensions
/extensions list
/extensions load extensions/demo/manifest.json
/extensions unload demo
/extensions reload demo
/extensions panel demo:status
/ext demo:hello text passed to the extension command
```

Discovery searches permitted JSON files under the selected project directory.
A file is a candidate only if it satisfies the version 1 extension manifest
schema: `version`, `name`, `entrypoint`, `files`, and `capabilities`. Manifest
filenames are unrestricted; `manifest.json` and `extension.json` are conventions,
not requirements. Ordinary or malformed JSON is silently skipped. The scan
respects ignore files, credential/generated exclusions, protected user state,
file read policy, symlink/hardlink restrictions and existing bounded repository
search limits. It examines at most 200 candidate JSON files, returns at most 32
matching manifests and 24 KB of metadata, and reports truncation. Narrow the
search directory when truncated.

Discovery is **metadata only**. It reads no declared Python entrypoint or resource
bytes and never imports extension code. It does not verify that declared source
files exist, that they are safe, or that the package is trusted. It prints names,
manifest paths, entrypoints, declared capabilities and file counts, plus a
`trust_required` marker. It intentionally cannot print a trusted snapshot digest
without reading the source. ASK/DENY files are omitted without prompting or
showing their names. If a required ignore file cannot be read without approval,
the scope is unavailable instead of silently ignoring its exclusions.

Before loading:

```text
/trust review extensions/demo/manifest.json
# Inspect all declared source files and review the displayed warning.
/trust allow EXACT_REVIEW_DIGEST
/extensions load extensions/demo/manifest.json
```

Trust grants bind the exact reviewed snapshot. Extension code runs with your host
user privileges; capabilities are declared Codine API surfaces, not filesystem,
network or subprocess sandbox permissions. Neither discovery nor this frontend
creates a trust grant. The runtime must validate the current snapshot against
its receipt before loading or reloading. Changed source needs a new review and
grant. Revocation prevents future trusted loads/actions according to the runtime
contract; unloading explicitly removes a loaded extension.

`/extensions list` asks the runtime for loaded names, snapshot digests, manifest
paths, registered tools/commands/events/panels and status. `/extensions panel`
prints a namespaced text panel. `/ext` splits the selector from the remaining
text and passes that text to the registered callback as data; the frontend does
not interpret shell syntax, Python, or templates. Both routes retain the normal
terminal's redaction and control-character sanitation.

Reload is delegated once to the runtime. A failed trust check or replacement
load is surfaced unchanged; the frontend never tries to unload first, guess a
new package, retry execution, or grant trust automatically. Atomic replacement
and preservation of previous registrations are runtime responsibilities. The
frontend does not implement or claim those guarantees by itself. If no extension
runtime is configured, discovery still works, and execution controls return a
clear unavailable message.

Targeted frontend checks:

```bash
python -m unittest tests.test_extension_commands tests.test_extension_trust tests.test_extension_lifecycle -v
```

The frontend tests exercise real manifest discovery/policy checks and fake
runtime dispatch. The lifecycle tests additionally start the real supervised
worker: load an explicitly trusted fixture, invoke its tool/command/panel, reject
untrusted or broken replacements while retaining the previous callable tool,
reload a newly trusted version, and unload its registrations. They also verify
busy-prompt and plan-mode restrictions. All fixtures are temporary; no model API
is called.
