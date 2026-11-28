# Model and endpoint capability checks

Codine checks required tool support before reserving request money or contacting
an inference endpoint. Both provider adapters repeat the check when called
directly, so evaluation code cannot silently bypass it. A local capability error
never triggers a retry with tools removed. The agent reports the capability
source in its status output.

## Evidence, scope, and unknowns

OpenRouter uses fresh records from the shared model catalog cache. Preflight
never refreshes that cache or makes metadata requests. Missing, expired, or
invalid metadata is unknown support, not proof of support or lack of support.
Use `/model refresh` to deliberately refresh catalog metadata.

A model's `supported_parameters` can cover several providers. It is not endpoint
verification. OpenRouter requests always keep `provider.require_parameters=true`:
the router must select a provider that supports the supplied parameters. Known
lack of `tools` fails locally. An uncached manually selected model remains usable
under that routing guard, with an explicit **unknown locally** status. This is
not a promise that the remote request will succeed. A remote rejection remains
subject to normal uncertain-charge accounting; HTTP status alone never releases
its reservation.

Selecting a particular OpenRouter endpoint discards the model-wide capability
union. A user declaration can state that endpoint's support. Otherwise the same
unknown/routing-guard policy applies. `provider.only` and `allow_fallbacks=false`
keep the request on the declared provider slug. No model fallback is introduced.

NVIDIA uses a direct endpoint and has no such routing enforcement. Unknown models
can receive plain-text requests, but requests with tools require an explicit
user declaration. The existing default
`nvidia/nemotron-3.5-super-vl-120b-a12b` has a built-in compatibility profile for
`https://integrate.api.nvidia.com/v1`: tools, temperature, top-p, seed, parallel
tool calls and the existing thinking template option. This preserves the
previous configuration; it is not a new live compatibility test. Any user
entry for that model replaces the built-in profile, rather than inheriting its
optional settings. No NVIDIA model catalog is assumed.

## User-owned declarations

Create `~/.config/codine/model-capabilities.json` manually. Its location is
independent of project `.env`, checkout configuration, and `CODINE_HOME`. The file
must be outside the active project, owned by the invoking user, not group/world
writable, and at most 64 KB. Symlink components, hardlinks and changes during the
read are rejected. Project instructions cannot supply these declarations.

Example (replace model/provider names with the actual names you intend to use):

```json
{
  "version": 1,
  "openrouter": {
    "vendor/model": {
      "endpoint": "provider-slug",
      "supported_parameters": ["tools", "temperature", "reasoning"],
      "max_output_tokens": 2048,
      "parameters": {
        "temperature": 0.4,
        "reasoning": {"effort": "low"}
      }
    }
  },
  "nvidia": {
    "vendor/model": {
      "endpoint": "https://integrate.api.nvidia.com/v1",
      "supported_parameters": ["tools", "temperature"],
      "parameters": {"temperature": 0.4}
    }
  }
}
```

Model IDs match exactly, with no wildcard. Endpoint is optional: omission means
OpenRouter routing or the fixed NVIDIA URL. A declaration is user-provided
information, not evidence of live provider behavior. Never put credentials in
this file; credential fields are rejected. `supported_parameters` may be omitted
to use fresh catalog support (OpenRouter) or retain unknown support. An empty
list explicitly declares no optional/tool support.

## Parameter adaptation

Requested optional parameters are sent only when recorded as supported. Others
are omitted and listed in status output. Supported declaration options are
`temperature`, `top_p`, `seed`, `parallel_tool_calls`, OpenRouter `reasoning`
(`effort`), and NVIDIA `thinking` (`enabled`). Thinking maps to NVIDIA's
`chat_template_kwargs.enable_thinking`; reasoning stays in OpenRouter's request
body. Neither option is converted across providers. Parallel tool calls are
omitted when there are no tool schemas.

The output cap, model, messages and streaming mode remain mandatory transport
settings. Codine never removes its output cap to make a request succeed. A valid
catalog `top_provider.max_completion_tokens` or explicit `max_output_tokens`
can lower the configured cap, never raise it. Existing router price ceilings
remain intact. Tool schemas and opaque reasoning in conversation history are
not rewritten or silently dropped by parameter adaptation.

Tests use fake SDK clients, temporary declaration files and temporary ledgers.
They verify pre-reservation rejection, endpoint scope, optional omission, cache
staleness, and unsafe declaration paths. No live capability probing or paid API
calls are performed by this feature or these tests.

References:

- [OpenRouter parameter-aware provider routing](https://github.com/OpenRouterTeam/docs/blob/main/guides/routing/provider-selection.mdx)
- [Catalog cache behavior](MODEL_CATALOG.md)
- [Streaming and reasoning preservation](OPENROUTER_STREAMING.md)
