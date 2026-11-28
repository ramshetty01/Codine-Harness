# Local MCP stdio tools

Codine supports **modern MCP 2026-07-28 and legacy MCP 2025-11-25 over local
stdio**, with explicit trust, paginated tool discovery and approved tool calls.
It is a bounded text-tool client, not universal MCP feature support. Streamable
HTTP, legacy SSE, OAuth, resources, prompts, subscriptions, tasks, elicitation,
sampling and automatic multi-round-trip continuations are not implemented.

With the default `protocol: "auto"`, Codine first sends `server/discover` with
modern per-request metadata. A valid result must advertise `2026-07-28` and a tools
capability. Each later request includes the same protocol version, empty client
capabilities and client identity in `params._meta`; there is no modern initialize
handshake. Server-reported identity or instructions do not grant authority.

Only a well-formed non-modern protocol error from that initial discovery probe
permits a single legacy initialize handshake on the same process. Recognized
modern errors (`-32020`, `-32021`, `-32022`) do not trigger a downgrade. A timeout,
malformed result, unsupported version or dead process stops the connection.
There is no automatic process restart or replay. For silent or fragile older
servers, explicitly set `protocol: "2025-11-25"` and review the changed trust
snapshot; that skips the modern probe. `protocol: "2026-07-28"` disables fallback.
Omitting this optional field preserves old configuration files and defaults to
auto detection. It is part of the reviewed launch definition.

Later `tools/list` or `tools/call` errors never cause a version change or retry.
Only complete results are accepted (an absent `resultType` is treated as complete
for compatibility). `input_required`, unknown result types and modern
server-to-client requests stop the connection without soliciting credentials,
calling a model, executing a client action or repeating the original tool call.
Modern requests advertise no optional client capabilities. Legacy sampling and
other server requests receive `Method not found` with no provider bridge.

Primary protocol references, checked November 28, 2026:

- [2025-11-25 lifecycle](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle)
- [stdio transport](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)
- [tool discovery and invocation](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)
- [2026-07-28 request metadata](https://modelcontextprotocol.io/specification/2026-07-28/basic/index)
- [Modern discovery](https://modelcontextprotocol.io/specification/2026-07-28/server/discover)
- [Modern stdio and cancellation](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/stdio)
- [2026-07-28 versioning and legacy compatibility](https://modelcontextprotocol.io/specification/2026-07-28/basic/versioning)

## Configure and review

Create `~/.config/codine/mcp/servers.json` yourself, outside model file tools. Its
containing directory must belong to you and reject shared writes; the file must
be private (`chmod 600`). Configuration never executes a server by itself.
Example (replace the paths with your own existing executable and server):

```json
{
  "version": 1,
  "servers": {
    "local": {
      "transport": "stdio",
      "protocol": "auto",
      "argv": ["/absolute/path/to/python", "-I", "-u", "/absolute/path/to/server.py"],
      "cwd": "/absolute/project",
      "files": ["/absolute/path/to/server.py"],
      "credential_env": ["SERVICE_API_TOKEN"]
    }
  }
}
```

Arguments are literal, with no shell interpolation or automatic package downloads.
Use an absolute executable and cwd. List launch scripts/resources whose changes
must invalidate approval. The executable and up to 16 declared files are hashed,
including canonical path and file identity, each bounded to 64 MiB. Declared files
are additionally limited to 64 MiB aggregate at launch. Their verified bytes are
copied to a private temporary directory before startup; whole argv tokens naming
a declared file (absolute or cwd-relative) are rewritten to the frozen copy.
The directory layout is preserved so declared sibling imports and resources
located relative to the staged script stay together. The stage is removed on
failure, timeout, cancellation or disconnect. The original configured cwd stays
in effect for server filesystem semantics.

Only explicit whole-path argv tokens are rewritten. Paths embedded in `-c` code,
`--option=path`, absolute paths inside scripts, or later cwd-relative reads are
not rewritten. Declare and pass the launch script as its own argv token to use
the frozen startup guarantee. The canonical host executable is fingerprinted
before/after staging and after discovery but is still opened by the OS at launch;
concurrent replacement of that executable is not atomically prevented. Imported
packages, undeclared dependencies, indirectly reopened original paths, files
subsequently opened by the server and remote services are **not attested**.
Declared source changes still invalidate future tool calls; freezing startup
bytes does not preserve permission to run after revocation or source changes.

`credential_env` names explicitly select existing shell credentials, never values
from model arguments or project dotenv. Names must contain TOKEN, KEY, SECRET,
PASSWORD or CREDENTIAL; runtime injection variables and Codine provider API-key
variables are forbidden. Values must be 8–8192 characters, are passed only to that
server and are added to output redaction. Never put secrets in argv or config.
Receipts contain configuration and hashes, not credential values. The child gets
only PATH, LANG and explicitly selected credentials, not the rest of Codine's
environment. Restart Codine to pick up changed shell credentials.

Use `/mcp list`, then `/mcp trust local` to inspect the exact launch definition,
file hashes, project identity and credential **names**. Interactive approval
creates a user-owned trust receipt. `/mcp connect local` explicitly starts the
reviewed server and discovers tools. `/mcp tools` shows registered names.
`/mcp disconnect local` closes it and removes tools. `/mcp revoke local` removes
all grants for that server/project and disconnects it. Changed definitions,
executables or declared scripts need a new trust review. Nothing auto-connects on
startup, resume or restart. Lifecycle commands run only at the idle prompt.

## Permissions and costs

Local servers run with your host-user privileges, **not in an OS sandbox**. They
can access host files, network and subprocesses independently of the protocol.
Do not trust a server solely because it claims its tools are read-only. Every
model-visible call requires explicit approval, even in automatic-edit mode;
planning mode hides and blocks MCP tools and prevents starting a server. Existing
Codine tools cannot be replaced by MCP names. Server output is untrusted data.

Codine advertises no sampling capability. Legacy server-to-client requests receive
`Method not found`; modern server-to-client requests are protocol violations and
close the connection. There is no path from MCP into the provider client or API
budget. Codine's own model requests retain the existing budget accounting.
A host-privileged MCP server may independently call paid services or read host
secrets. Those services are not covered by Codine's model budget, and this client
cannot enforce a financial limit on arbitrary server code.

## Bounds and recovery

- Eight configured servers; 32 discovered tools/server; four discovery pages.
- Ten-second request deadlines; 64 KiB JSON frames and outgoing requests;
  512 KiB aggregate stdout and 16 KiB stderr per exchange; 128 frames/exchange.
- Input schemas use the existing bounded primitive/object/array subset. Unsupported
  schemas fail connection atomically. Missing `additionalProperties` is narrowed
  to false locally; unknown arguments are not forwarded. `$schema` is ignored as
  a dialect label; `$ref`, combinators and unsupported keywords are rejected.
- Tool results support text content plus JSON structured content, not images,
  audio or embedded resources. No result URL or resource is fetched automatically.
- Tool-list change notifications require explicit reconnect before another call.
- Protocol errors, disconnection, timeout and cancellation retire the connection;
  actions are never automatically replayed. A tool's declared `isError` remains a
  tool result rather than triggering a retry.

Shutdown closes stdin, briefly waits for graceful exit, then sends bounded
termination and kills the owned process group, reaps the direct child and closes
pipes. Servers that deliberately detach processes are not sandboxed by this
mechanism. Lifecycle tests use real local fixture processes and fake credentials;
no model inference or live service credentials are required.

## Modern stdio verification and limits

Cancellation and timeout send a best-effort `notifications/cancelled` with the
exact outstanding request ID, bounded to 100 ms, before process-tree cleanup.
No cancellation is sent for an unsent or already completed request. Closing stdin
and bounded termination still handle servers that ignore cancellation or stop
reading. There is no automatic retry of a tool whose outcome may be unknown.

Tool schemas support a restricted object/array/scalar subset of JSON Schema
2020-12, not every valid schema in that dialect. Unsupported dialects, composition
keywords and references fail closed; no external `$ref` is fetched. Only text
content is exposed. Reconnect explicitly to refresh tool metadata; modern
subscription streams and their list-change notifications are not subscribed to.

Run `python -m unittest tests.test_mcp tests.test_mcp_modern -v` for real subprocess
fixtures exercising modern discovery/metadata, legacy fallback, explicit-version
trust changes, recognized modern errors, malformed discovery, input-required
refusal without replay, silent-probe recovery guidance, and cancellation IDs and
cleanup. Existing trust, staging, credential, approval and plan-policy tests stay
in the same gate. These tests make no model requests or network calls. This
completes a documented local stdio integration, not HTTP/OAuth interoperability or
a claim of full MCP conformance.


The frame parser rejects duplicate JSON fields before protocol classification,
so an ambiguous error code cannot trigger a legacy fallback. Incoming JSON is
bounded to 32 nesting levels and 10,000 values before recursive sanitization;
optional result `_meta` must be an object. Malformed frames retire the connection
without replaying tools. An explicit idle `/mcp connect` uses its own cancellation
scope and restores the prior turn's cancellation flag afterward; it does not
clear cancellation for a later model action. Legacy and modern subprocess tests
cover these boundaries alongside the original integration cases.
