# Static symbol navigation

Ask Codine to find definitions, inspect reference candidates, or locate Python
syntax errors. The `navigate_symbols` tool works in normal and planning modes.
It reads approved project snapshots without importing or executing project code.

```json
{"action": "definitions", "symbol": "Formatter.normalize", "path": "package", "glob": "*.py"}
```

Definitions include functions, async functions, classes, methods, simple name
bindings, parameters and import bindings. An empty symbol lists definitions.
A query matches the short name or the suffix of the qualified name. Results
include project path, structural scope, definition kind, line, column, ending
line and the snapshot SHA256. Locations are one-based Unicode character columns;
function/class locations start at their defining statement. Long displayed
names/scopes are clipped and marked `label_truncated`.

```json
{"action": "references", "symbol": "normalize", "path": ".", "limit": 50}
```

References are **lexical candidates**, not resolved targets. They include load
names, loaded attributes and imported names. A dotted query uses its final name
for candidate matching. `obj.normalize()` and a shadowed local `normalize()` may
both appear; an aliased call `clean()` requires searching `clean` separately.
Imports, scopes and current source must be read before deciding what a rename or
behavior change affects. The tool never grants edit/read-coverage authorization.
Use `read_file` to inspect complete regions before editing them.

Scope describes structural AST containment, not Python binding semantics. The
tool does not infer receiver types, follow reexports, resolve dynamic attributes,
resolve closures/comprehensions, calculate call graphs or perform safe renames.
Decorators/default expressions may be structurally associated with their enclosing
function/class even though Python evaluates them in a different binding scope.
There is no LSP service, project interpreter execution or automatic dependency
installation.

```json
{"action": "diagnostics", "path": "package", "glob": "*.py"}
```

Diagnostics report Python **parse errors** and parser-limit failures, with locations
when available. They are not a linter, type checker, full compilation check or test
run. Parsing uses the Python version running Codine: newer syntax unsupported by
that interpreter can produce a diagnostic. `.py` and `.pyi` are supported. A
syntax error makes definition/reference coverage incomplete for that file; valid
files can still return useful results. Diagnostics do not include source-line
contents automatically.

## Other languages

```json
{"action": "references", "symbol": "normalize", "glob": "*.js", "language": "text"}
```

The explicit text mode uses bounded literal repository search, includes comments
and strings, and labels results `literal_text_fallback`. It does not identify
semantic definitions/references or offer diagnostics. Python mode reports how many
non-Python candidates it skipped and suggests this fallback. Full multi-language
semantic navigation remains unsupported; the tool never presents text matches as
LSP results.

## Access and resource bounds

Discovery uses the existing descriptor-based repository scanner and isolated
`.gitignore` matching, including nested rules and negations. Credential/internal
paths, denied files and links are excluded. Explicit file read ASK rules run before
source enters the parser. Declined/unreadable files produce an aggregate skipped
count, without returning their contents or symbol names. Navigation does not modify
`read_versions`, read coverage, lifecycle fingerprints, files or conversation state.

Python parsing runs in a separate `python -I` process in an empty temporary working
directory, with a minimal environment and no provider credentials. Source is JSON
data passed to `ast.parse`; it is never imported or evaluated. The worker has a
2-second wall deadline and attempts a 2-second CPU ceiling. Linux also enforces a
512 MB address-space ceiling; other supported platforms rely on the hard deadline
and bounded source/node allowances rather than claiming that memory limit.

The Python tool admits at most 200 source files, 256 KB per file, 2 MB of total
source, 40,000 AST nodes per file and 1–200 result/diagnostic rows. The worker
limits encoded output to 900 KB; oversized output yields an explicit output-limit
result instead of unverified partial rows. Repository
scanner limits still apply. Source limits, skipped reads, timeout, syntax errors
and result caps are explicitly reported; narrow path/glob to continue. A timed-out
or failed worker returns no unverified partial worker results and never retries
inference. Text fallback uses the existing repository-search limits instead.

Returned snapshots may already be stale; hashes identify what was read. Existing
Agent output handling automatically offloads sufficiently large navigation output
to a bounded artifact, which can be inspected with `/artifact` or tool history.
Artifact/history reads also do not authorize edits.

## Representative acceptance task

`tests/fixtures/navigation` contains two Python modules with a module function,
a same-named class method, an imported alias and a shadowed parameter, plus a
JavaScript textual match. The test locates distinct qualified definitions and
checks that reference candidates retain the ambiguity instead of promising a
safe rename. Another fixture introduces a broken function and verifies the exact
syntax-error line without executing neighboring top-level code.

Offline tests also cover Unicode columns, ignores, secret/link exclusion, declined
read permission before parsing, parser deadlines and node/source/result limits,
planning-mode availability, no edit authorization and artifact offload. No model
API is needed for this feature or its acceptance tests.
