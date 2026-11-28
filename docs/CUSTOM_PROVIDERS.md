# Explicit custom OpenAI-compatible providers

Codine supports NVIDIA and OpenRouter through their existing fixed adapters, plus
an explicitly selected `custom` adapter for compatible Chat Completions streaming
servers. This is a protocol integration, not a claim that every compatible server
supports tools, reasoning, usage reporting or identical model behavior.

For a local server you already run:

```sh
codine --provider custom --model your-model \
  --endpoint http://127.0.0.1:8000/v1 --allow-unmetered-custom
```

The acknowledgement is required even on loopback. **Cost is unknown, not zero.**
Codine does not enforce a USD spending cap for custom endpoints. Token ceilings
still apply. Custom response `usage.cost` is not accepted as verified billing
information; available token counts remain visible. Existing OpenRouter ledgers
are neither reset nor reused as a custom-provider allowance. Use the fixed
OpenRouter adapter for OpenRouter accounting.

Trusted shell or user configuration may set `CODINE_PROVIDER=custom`,
`CODINE_CUSTOM_MODEL`, `CODINE_CUSTOM_ENDPOINT`, `CODINE_CUSTOM_API_KEY`, and
`CODINE_CUSTOM_UNMETERED=true`. Project `.env` and legacy checkout `.env` cannot
supply these custom routing/credential choices. Explicit CLI options override
trusted configuration. Settings are startup snapshots, not dynamically reloaded.
API keys never enter session data. Missing loopback authentication uses the
nonsecret `codine-local-no-auth` SDK placeholder; remote endpoints require an
explicit custom credential. Known OpenRouter/NVIDIA keys cannot be reused.

Remote endpoints require HTTPS; plain HTTP requires a literal loopback IP.
URLs with userinfo, query strings, fragments, ambiguous path components, or known
OpenRouter/NVIDIA origins are rejected. Redirects, environment proxies and model
fallbacks are disabled. An endpoint alias owned by another service cannot be
identified automatically; only configure endpoints you trust. Prepared requests
capture endpoint and authentication before dispatch, so later object mutation
cannot redirect that request. Only established pre-dispatch connection failures
have the existing bounded retry policy; response/read failures are not replayed.

## Tools and optional parameters

Custom tool support must be explicitly declared in the user-owned
`~/.config/codine/model-capabilities.json`, with the exact canonical endpoint:

```json
{"version":1,"custom":{"your-model":{
  "endpoint":"http://127.0.0.1:8000/v1",
  "supported_parameters":["tools","temperature"],
  "parameters":{"temperature":0.2}
}}}
```

Keep this file private and outside the selected project. A declaration is a user
assertion, not provider verification. Unknown or unsupported required tools fail
before dispatch. Unknown optional parameters are omitted. Responses use the same
bounded streamed tool-fragment reconstruction and opaque reasoning preservation
as OpenRouter; incomplete completions cannot execute requested tools.

## Resume and integration

Custom sessions store their canonical endpoint binding, not credentials. Resume
requires the same endpoint and a trusted unmetered acknowledgement on launch.
Explicit `/restart` carries the already-authorized endpoint and acknowledgement;
`/new`, reviewed transcript import and context forks retain endpoint identity.
`/budget` and status report unknown custom cost without implying a dollar cap.

`CustomProvider(config)` implements `prepare_completion` / `complete` / `close`.
`Config.load(..., endpoint=..., allow_unmetered_custom=...)` is the shared startup
validation boundary. SDK/RPC callers must explicitly opt in; constructing a
client must never implicitly acknowledge unmetered usage. Compatibility tests
use temporary fake HTTP servers only and make no provider inference calls.
