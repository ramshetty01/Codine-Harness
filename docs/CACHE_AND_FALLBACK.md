# Cache measurements and explicit alternatives

`/cache` displays provider-reported cache-read and cache-write token counts and
coverage. `/usage` includes both fields. Missing fields remain unknown; an
explicit zero is a measured zero. Older sessions have unknown cache-write
coverage until new responses report it. Neither command estimates dollar savings.
The existing `/budget` remains the spending authority. Interrupted responses
without final usage do not establish a cache hit or a free request.

OpenRouter documents `usage.prompt_tokens_details.cached_tokens` and
`cache_write_tokens`. Automatic caching, minimum prompt sizes, expiration and
write pricing vary by upstream model/provider. Codine does not inject universal
cache-control markers or promise caching on unsupported models. See the official
[prompt caching documentation](https://openrouter.ai/docs/guides/best-practices/prompt-caching).

Run the offline measurement demonstration:

```sh
python -m codine.cache_evals --offline
```

It labels its synthetic values explicitly. The reusable `measure` function sends
2–10 independent copies of identical inputs to an injected completion callback,
records observed latency and reported cache counts, and stops immediately on an
exception or requested tool action. A read fraction is calculated only when that
same response reports consistent input/read counts. Repeated input and lower
latency do not establish causation or savings. There is **no live benchmark CLI**
in this milestone: a future final-budget evaluation must supply the existing
budget-guarded completion callback, preserve uncertain reservations, and stop
when any spending guard fails. This work ran no live cache benchmark.

## Explicit fallback policy

Codine never retries an uncertain billable request with another model. It never
automatically changes the selected model/provider. OpenRouter requests explicitly
set `provider.allow_fallbacks=false`, while retaining required-parameter and price
constraints. This requests no upstream endpoint failover within that request; it
is not a guarantee of the same physical endpoint across successive requests.
See official [provider routing](https://openrouter.ai/docs/guides/routing/provider-selection).

Optional private user configuration at `~/.config/codine/fallbacks.json` suggests
same-provider alternatives (maximum 20 per provider):

```json
{"version":1,"providers":{"openrouter":["vendor/alternative"]}}
```

Project configuration cannot supply this policy, and generic workspace tools
cannot change its protected path. Suggestions are not auto-selection authority:

```text
/fallback list
/fallback review vendor/alternative
/fallback confirm DIGEST_FROM_REVIEW
```

An explicitly typed model need not be in the suggestions. Review preflights tools
without inference. Confirmation requires unchanged session state and settled local
and shared spending. It creates a child session with the full historical archive
intact, selects the same-provider alternative, and clears active context, summary
and task plan. Billing and usage remain current; shared spending never resets.
Files, project memory and prior external effects stay unchanged. Read/skill and
notebook receipts reset through the ordinary session-switch path. No old tool
call, partial response or user request is replayed: explicitly restate the task.

`/model` still supports ordinary explicit model changes, but blocks changes while
spending/turn state is pending or when active provider reasoning may be
model-bound. Use the reviewed fresh-context child in that case. Plain reasoning
is conservatively treated as potentially model-specific; it is not mislabeled as
encrypted. Opaque reasoning and complete tool-call/result pairs stay unchanged in
the original and child archives. Changing providers requires a separate explicit
startup with that provider's credential and accounting controls.
