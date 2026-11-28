# Supervised extension workers

Extension Python runs with the host user's privileges. A child process and the
registration API provide lifecycle isolation, **not an OS sandbox**. An extension
can read host files, use the network or spawn processes independently of its
registered capabilities. Only explicitly trusted code should run.

`ExtensionProcess(snapshot, project, trust_store, redactor, cancel_event=None)`
checks an exact trust receipt before staging and again before starting code.
It verifies the snapshot digest, project identity, file paths, hashes and bounds,
then writes only the declared snapshot bytes to a private temporary directory.
The loader must obtain a fresh snapshot before constructing the process. Staging
never rereads source paths: edits after review do not silently replace approved
bytes. `describe()` and every `invoke(kind, name, payload=None)` recheck the
receipt, so revoked grants cannot authorize another action on a running worker.

The child uses the same interpreter with `-I -u`, an explicit trusted bridge
script, and a minimal PATH/LANG environment. Codine API keys, HOME, PYTHONPATH and
other ambient variables are not inherited. The trusted SDK directory and staged
package directory are explicitly added to import paths; this supports declared
helper modules. This does not prevent trusted Python code from accessing other
host resources or changing its import paths.

The entrypoint must define `register(api)`. Registration occurs before any
invocation; normal `print()` output goes to stderr. The bridge exchanges version
1 JSON lines with monotonically increasing request IDs. Parent supervision drains
stdout and stderr while writing requests without blocking indefinitely. Limits:

- Registration: 5 seconds; invocation: 10 seconds.
- Request and response envelopes: 64 KiB each, including JSON escaping.
- Diagnostic stderr: 16 KiB per exchange.
- Snapshot source/data: 32 declared files and the manifest's 1 MiB limit.

A malformed response, oversized output, disconnection, timeout or handler error
retires the worker. Actions are never replayed automatically because a handler
may have performed side effects before failing. A request rejected as too large
before transmission does not retire the idle worker. Error and response text is
redacted and terminal control characters are sanitized before returning to the
host. Normal stderr is available as sanitized `worker.stderr` diagnostics.

Cancellation raises `KeyboardInterrupt` after process-group cleanup. `close()`
is idempotent, kills the worker's process group, reaps the direct child, closes
pipes and removes the staged directory. This handles ordinary descendants that
remain in the process group; host-privileged code can deliberately detach and is
not contained by this mechanism. No process sandbox or guaranteed rollback of
extension side effects is claimed.

The host controls when events run. In particular, invoke `turn_end` only after
the corresponding turn is durably saved. The bridge dispatches only explicit
requests and does not emit its own session or lifecycle events.

Offline tests launch actual trusted fixture workers for tools, commands, events
and UI panels; verify ordering, redaction, exact staged bytes and key omission;
and exercise registration failure, timeout, cancellation, descendant cleanup,
revocation and request/output bounds without provider API calls.
