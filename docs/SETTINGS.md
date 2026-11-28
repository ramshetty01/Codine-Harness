# Settings and their sources

`/settings` shows an allowlisted, redacted configuration snapshot and a source
label for each value. It does not reload files, call a provider, or inspect budget
ledgers. API keys are shown **only as a source**, never as their value. Model-limit
map keys are omitted; the active model's effective local context/output ceilings
are shown separately. `/model` and `/thinking` runtime overrides are identified.

For ordinary settings, precedence is:

1. Explicit CLI provider/model options.
2. The invoking process environment (exported shell settings).
3. The selected project's `.env`.
4. The user configuration `.env`.
5. A legacy checkout `.env`, when applicable.
6. Built-in defaults.

An exported shell setting wins over a project's preference. Empty strings are
explicit values (an empty API key disables it); dotenv names without `=` are
ignored. `${NAME}` interpolation is disabled: settings cannot pull arbitrary
shell secrets into another field. Invalid values produce startup errors without
echoing their contents. Files must be stable, single-link regular files under
64 KB; symlinks are rejected.

## Locations and compatibility

For an installed package, the user file is `$CODINE_HOME/.env` when that shell
override is set; otherwise it is `$XDG_CONFIG_HOME/codine/.env`, defaulting to
`~/.config/codine/.env`. A development checkout without `CODINE_HOME` reads the
fixed user `~/.config/codine/.env`, then uses the checkout's `.env` as a lower
priority **legacy checkout** fallback. That fallback is not mislabeled global
user configuration. A file that is also the selected project's `.env` is treated
as project configuration, not a trusted budget baseline under another label.

A project's `.env` may contain `NVIDIA_API_KEY` or `OPENROUTER_API_KEY`, preserving
the existing local-key workflow. Provider endpoints remain fixed in code; project
settings cannot redirect authentication. Dotenv maps never modify `os.environ`.
Thus project entries for `HOME`, `CODINE_HOME`, XDG roots, proxies, editor commands,
policy locations, or forwarded execution credentials have no effect. Only the
explicit Config setting names are used. Explicit execution profiles still capture
named credentials from the invoking shell; project dotenv values are not promoted
to shell credentials or inherited by subprocesses.

Recognized names are `CODINE_PROVIDER`, `NVIDIA_MODEL`, `OPENROUTER_MODEL`, the two
provider API key names, `CODINE_MAX_TOKENS`, `CODINE_MAX_STEPS`,
`CODINE_CONTEXT_CHARS`, `CODINE_CONTEXT_TOKENS`, `CODINE_MODEL_LIMITS`,
`CODINE_BUDGET_USD`, and `CODINE_REQUEST_BUDGET_USD`. Loaded secret-like values are
retained privately for redaction, including overridden values, without printing
the environment or serializing configuration into sessions.

## Budgets are deliberately different

Budget values first resolve through shell, user, legacy, and defaults. A project's
budget or request cap can lower that trusted value, never increase it. The source
label explains when a project tightens a cap or an attempted increase is ignored.
All amounts must be valid and the request cap cannot exceed the session limit.

These configuration values initialize **new** session ledgers. Existing session
limits, spending, unresolved charges, and the shared allowance remain authoritative;
loading settings never rewrites them. A lower configured request cap still feeds
the existing shared per-request guard. A changed total session budget is not an
implicit migration of a resumed ledger. Use `/budget` for active ledger limits
and explicit reconciliation; `/settings` is not a claim that those limits changed.

## Reload and resume

Restart to apply file or shell changes. Switching models or sessions does not
silently reload dotenv files midway through a request. Startup `--resume` selects
the saved provider/model unless explicitly overridden consistently, and `/settings`
labels those values as saved-session choices. No provider call is made during
configuration loading. Tests use temporary dotenv files and fake credentials to
verify precedence, provenance, redaction, trust roots, cross-project isolation,
startup validation, and budget tightening without touching real ledgers.
