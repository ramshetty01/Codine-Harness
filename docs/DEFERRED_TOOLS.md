# Deferred integration tool discovery

Connected MCP servers and loaded extensions may expose many tools. Codine keeps
those tools out of model request schemas until explicitly selected. Core file,
command, planning and discovery tools remain available normally. Connecting a
server or loading an extension still requires the existing explicit user trust
and lifecycle actions; discovery never starts a process or grants trust.

The model can use three core tools:

1. `discover_tools(query="", limit=10)` searches already connected, currently
   permitted integration metadata. Results contain names, descriptions truncated
   to 240 characters, active status and an opaque registration revision. They do
   not contain full input schemas. Query length is at most 200 characters and the
   result limit is 1–20. Refine the query when the result is truncated.
2. `load_tool(name, version)` requests the exact discovered schema. The result is
   `scheduled`; activation occurs only after **all assistant/tool results in that
   batch are durably saved**. Calling the integration in the same batch fails.
   Its full schema is available in the next model request.
3. `unload_tools(names)` schedules removal of integration schemas at the same
   completed batch boundary. Core tools cannot be unloaded this way. Servers
   remain connected; unloading a schema does not stop a process.

The defaults are eight active integration schemas and 32 KiB of combined schema
JSON. An embedding application can set `ToolRegistry(deferred_limit=...,
deferred_bytes=...)`: supported bounds are 1–32 schemas and 1–128 KiB. These are
local maximums, not provider context promises. The actual agent context preflight
and context inspector include all loaded schemas and the configured output
reservation. If context cannot fit, no model request bypasses that check. To
recover without another model call, disconnect the MCP server with `/mcp disconnect NAME` or unload the extension with `/extensions unload NAME`.

A revision binds the tool's exact declaration and a fresh registration identity.
Replacing or reloading a tool invalidates earlier revisions and activations even
when its name and schema are unchanged. In-place schema changes invalidate them
too. Revoked grants, closed workers, known MCP tool-list changes and planning
restrictions remove affected tools from discovery/advertising and prevent their
invocation. Local availability checks do not execute child code, call network
services or prompt for permissions. Actual integration handlers still recheck
trust and require their existing per-call approval. Tool descriptions remain
untrusted integration metadata, not authority.

Activation state lives only in the current process. Session archives preserve
historical discovery/load results but cannot restore a registration, trust grant
or active schema. Cancellation and persistence faults discard pending changes.
A resumed or reloaded integration must be discovered again as needed; old model
messages containing a tool name cannot authorize hidden-tool execution.

Offline tests cover the real Agent discovery → scheduled load → durable boundary
→ invocation path, a malicious same-batch call, changed registrations, real
extension reload/revocation, plan restrictions, count/byte/context bounds,
unavailable tools, cancellation and save faults. Existing MCP and extension
permission tests explicitly activate schemas before testing execution approval;
no approval checks were removed.
