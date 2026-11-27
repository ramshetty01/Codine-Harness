# Bounded repository search (R2)

`list_files` now accepts `glob` and `respect_ignore`. `search` adds the same filters,
plus `regex` and `case_sensitive`. Existing literal search calls remain compatible.
Examples of model tool arguments:

```json
{"glob": "**/*.py", "path": "src", "limit": 100}
```

```json
{"query": "^class [A-Z]", "regex": true, "glob": "**/*.py", "limit": 20}
```

A glob without `/` matches basenames at any depth (`*.js`). A glob with `/` is
relative to the project root: `src/*.py` matches one level, while `src/**/*.py`
matches zero or more directory levels. `*`, `?`, character classes and `**` are
supported; absolute paths, traversal, backslashes and oversized patterns are not.

Content results contain project-relative path, one-based line and column,
`text_start_column`, and a snippet of at most 1000 characters centered near the
match. Read the actual region before editing; snippets do not register a complete
read for edit authorization. Case-insensitive literals use Unicode case folding;
regex mode uses Python's `re.IGNORECASE` semantics.

## Ignore rules without running project configuration

By default, root and nested `.gitignore` files apply, including Git's negation and
ignored-parent behavior. The implementation reads ignore text through the existing
no-follow, single-link, bounded workspace reader. It copies only this text into a
temporary tree with minimal isolated Git metadata, then invokes
[`git check-ignore --no-index --stdin -z`](https://git-scm.com/docs/git-check-ignore)
with explicit argv, no shell, a one-second timeout and a stripped environment.
The project's `.git/config`, index, hooks, credential helpers, global excludes and
`.git/info/exclude` are not used. System/global configuration and filesystem monitor
helpers are disabled. A Git executable inside the model-editable project is
refused, preventing PATH shadowing by a candidate file. No candidate content is
copied to the ignore matcher.

The same `.gitignore` semantics work in directories that are **not Git repositories**.
Because there is no index, patterns also apply to names that would be tracked by
Git. This is a search filtering policy, not an emulation of `git status`. See Git's
[ignore pattern rules](https://git-scm.com/docs/gitignore). Git must be installed
when ignore files exist. Missing Git, unsafe/oversized ignore files or matcher
failure stops the operation instead of silently returning unfiltered results.

`respect_ignore=false` explicitly bypasses ignore patterns, but never bypasses
credential, internal directory, link or configured read-policy restrictions.
Ignore patterns are navigation filters, not a security boundary.

## Bounds and failure behavior

- At most 20,000 directory entries and 4,000 matching file candidates per scan;
  directory traversal checks a two-second deadline.
- Listing returns at most 2,000 names; content search returns at most 200 matches.
- Search loads at most 8 MB of UTF-8 content; individual reads retain the existing
  2 MB file limit. Binary, unsafe and policy-denied files are skipped.
- At most 64 ignore files, 16 KB each and 128 KB combined. Narrowing the search still
  loads applicable ancestor ignore files.
- Regexes are at most 1024 characters and execute in an isolated Python process
  with a one-second deadline. A catastrophic expression cannot trap the main agent
  loop. On timeout the worker is killed, results are empty, and `regex_timeout`
  reports incomplete coverage. Invalid patterns produce an error.
- Glob patterns are at most 256 characters. Directory traversal uses no-follow
  descriptors; returned candidates are checked through workspace policy and actual
  content reads retain descriptor-based link/race protections.

Truncation reports `scan_limit`, `file_limit`, `deadline`, `byte_limit`,
`result_limit` or `regex_timeout` rather than claiming exhaustive coverage. Ignored
subtrees may consume discovery allowance before filtering; narrow `path`/`glob` in
very large repositories. No skipped excluded filename is returned as a diagnostic.
Filesystem I/O and interactive read approval are not given a hard wall-clock bound;
regex execution itself is bounded independently. This is not an OS sandbox.

Regression tests cover nested/negated ignores in a non-Git project, isolated Git
configuration, globs, literal/regex case handling, malformed/catastrophic regexes,
large-line match previews, scan/file/byte/match caps, credential link exclusions,
and a directory symlink swapped immediately before open. No model API is used.
