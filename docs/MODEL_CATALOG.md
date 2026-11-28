# Deliberate model selection

`/model` lists OpenRouter tool-capable text models with advertised prompt/output
prices. `/model free` filters advertised zero-cost routes; a `:free` suffix alone
is not enough, and missing prices never become zero. Unknown prices cannot
establish that a route is free. Provider quotas and additional charges can change.

The picker uses a fresh local catalog for up to one hour. Otherwise it attempts a
metadata refresh (no inference request). Its notice, also shown inside the picker,
includes source, age and freshness. `/model refresh` (or `/model refresh free`)
forces that metadata request. Cancelling the picker leaves the model unchanged.

If refresh fails, Codine does not silently substitute a model, provider or old
prices. Use `/model cached` or `/model cached free` to **explicitly** review the
cached catalog, including expired entries. STALE is shown visibly. Stale records
may describe models that were removed, changed capability or changed price.

Alternatively use `/model vendor/exact-id` to manually select an exact model
without metadata. This is not a claim that the model exists or supports tools.
Provider capabilities and inference budget checks still apply. Select providers
explicitly at startup with `--provider nvidia|openrouter`; there is no automatic
provider fallback. Selecting a model never resets accumulated cost or pending
reservations.

Only public model records are cached, not API keys, credit balances or key
metadata. Cache files live in `STATE_ROOT/catalogs` (the existing user state
location), with provider and exact endpoint hashed into the filename and verified
inside the document. Directories are private (0700), files private (0600), and
writes use temporary-file fsync plus atomic replacement. Symlinks, hardlinked
files, wrong owners/permissions, future timestamps, duplicate keys and malformed
schema are rejected. Limits: 8 MB, 10,000 models, bounded metadata fields.
If cache writes fail, the freshly retrieved catalog remains usable for that one
selection with a visible warning. Existing valid cache bytes survive failed writes.

The cache is a convenience, not a financial authority. Evaluation and interactive
shared-budget code keep their live metadata/cost checks; stale UI selection does
not bypass them or guarantee a free inference. Capability consumers can use
`CatalogCache().model_record(exact_id)` offline; it rejects expired/invalid cache
and never performs a network request. Missing records return `None`.

Run offline verification with:

```sh
python -m unittest tests.test_catalog_cache tests.test_router_catalog tests.test_model_picker
```
