# Discover and run repository checks

Use `/checks` (or `/checks subdirectory`) to inspect supported configuration without running code. Each suggestion includes a command argv, category, source path and SHA256, and stable discovery ID. `/checks results` displays the last 32 invocation summaries for this process. Model tools are `discover_checks(cwd=".")` and `run_check(check_id, timeout=30, files=...)`. Full outcomes remain in ordinary tool history; discovery catalogs and the summaries are process-local and cleared when switching sessions.

Recognized configurations:

| Source | Suggested commands |
|---|---|
| pytest INI/TOML, pyproject pytest, tox/setup pytest sections | `python -m pytest` |
| pyproject Ruff or ruff.toml | `python -m ruff check .` |
| mypy INI/pyproject | `python -m mypy .` |
| setup/tox flake8 section | `python -m flake8 .` |
| package.json test/lint/typecheck/type-check scripts and named variants | `npm run NAME` |
| Cargo package/workspace manifest | `cargo test`, `cargo check` |
| go.mod | `go test ./...`, `go vet ./...` |
| Explicit Make test/check/lint/typecheck/type-check targets | `make TARGET` |

These are suggestions from configuration, not proof that dependencies are installed or that the repository supports a particular runtime. Configure an appropriate execution image/environment. Discovery does not run package managers, evaluate Make recipes, install packages, search arbitrary parent directories, or guess from model text. npm/Make/Cargo/Python tooling may execute repository code, plugins or lifecycle hooks when a check is approved.

Discovery uses existing file exclusions and permissions, skips automatically reading files requiring approval, and reports malformed/unavailable configurations without exposing their contents in diagnostics. It inspects only the documented names in the selected directory, each at most 128 KB, retaining at most 64 suggestions. Configurations changing since discovery or while approval is pending require rediscovery before execution.

Execution uses the same host/Docker policy, planning-mode restriction, profiles, output cap, timeout and cleanup path as `run_command`. Docker requires an explicit snapshot file list containing the source configuration; no implicit whole-repository staging or host fallback occurs. Source hash and executor are returned with each result.

A result is `passed` only for a completed invocation with integer exit code zero. A completed nonzero exit is `failed`. Unavailable runtimes/OS transport errors (`execution_error`), decline, denial, timeout, cancellation and incomplete statuses are `not_verified`, including timeouts with an otherwise zero exit. This verifies the invocation, not test coverage, assertion counts, task completion, or model quality. Discovery alone is never a passing check.

Tests cover multi-language recognition, no execution during discovery, malformed/private configuration, directory scope, stale changes before/after approval, planning/decline, real Make-launched Python pass/failure, timeout classification, and Docker snapshot dispatch using its existing executor interface.

References: [pytest configuration](https://docs.pytest.org/en/stable/reference/customize.html), [Cargo test](https://doc.rust-lang.org/cargo/commands/cargo-test.html).
