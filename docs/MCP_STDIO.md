# Local MCP stdio tools

Codine supports **MCP 2025-11-25 over local stdio**, explicitly selected through
`initialize`, followed by `notifications/initialized`, paginated `tools/list` and
approved `tools/call` requests. This is a useful scoped client, not universal MCP
support. Current MCP 2026-07-28 uses different per-request metadata semantics;
Codine does not implement that revision. An unsupported negotiated version fails
closed. Streamable HTTP, legacy SSE, OAuth, resources, prompts, tasks, sampling,
elicitation and server-driven client actions are not implemented.

Primary protocol references, checked November 28, 2026:

- [2025-11-25 lifecycle](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle)
- [stdio transport](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)
- [tool discovery and invocation](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)
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
including canonical path and file identity, each bounded to 64 MiB. Imported
packages, undeclared dependencies, files subsequently opened by the server and
remote services are **not attested** by these hashes. Hashes are rechecked before
launch and tool calls; this is not an atomic filesystem execution guarantee
against arbitrary concurrent replacement between checking and execution.

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

Codine advertises no sampling capability and refuses server-to-client requests
with `Method not found`; there is no path from MCP into the provider client or
API budget. Codine's own model requests retain the existing budget accounting.
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
