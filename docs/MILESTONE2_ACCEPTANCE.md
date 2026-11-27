# Milestone 2: sandboxed repository execution acceptance

The milestone gate is the concrete acceptance statement in
[GAP_ANALYSIS.md](GAP_ANALYSIS.md): a repository test runs inside the sandbox,
deliberate filesystem/network boundary violations are refused, cancellation
removes descendants, and failures never silently fall back to host execution.
The milestone description also calls for real build execution.

## Reproduce without inference

From the checkout, with dependencies installed, Docker running, and the local
`python:3.12-slim` image already present:

```bash
python scripts/check_sandbox_acceptance.py
python -m codine.command_evals --offline
```

The first command enables real Docker tests explicitly, prints the resolved image
ID, and runs the E1–E8 acceptance suites. Any failure, missing Docker/image, or
skipped test fails the gate. It never pulls an image or falls back to host Python
for container candidates. Host-specific tests intentionally exercise the separate
approved host executor; that is not a container fallback.

The second command validates five pinned, repository-derived tasks with actual
public/final command invocations, known-broken baselines and reference repairs.
Every attempt is retained under `.codine/evals/command-attempts/`. This is fixture
and execution evidence, **not a model solve rate**. Neither command makes inference
requests or needs a provider API key.

## Evidence map

| Acceptance area | Existing executable evidence |
|---|---|
| E1 visible explicit executor and no fallback | `test_execution_mode.ExecutionModeTests.test_cli_resume_retains_docker_and_unavailable_never_falls_back`; `test_sandbox.SandboxPolicyTests.test_missing_docker_fails_closed`; `test_registry_executes_approved_snapshot_in_real_docker` |
| E2 selected immutable repository input | `test_staging.ManifestTests`; `test_execution_mode.ExecutionModeTests.test_approval_uses_frozen_bytes_even_if_host_file_changes`; repository manifest tests reject links, special files, traversal and limits |
| E3 real tests, cwd, environment, status | `test_repository_executor.RepositoryCommandDockerTests.test_actual_unittest_success_and_failure_exit_status`; `test_cwd_explicit_env_and_no_host_secret_or_mount`; the separate `codine.command_evals --offline` pack |
| E3 actual build artifact | `test_milestone2_acceptance.SandboxBuildAcceptanceTests.test_build_python_archive_and_run_its_smoke_test_inside_container` creates a Python archive with `python -m zipapp`, runs it, checks its output, and confirms no artifact appears on the host |
| E4 prepared dependency and reusable cache | `test_prepare_environment.RealPreparationTests.test_prepared_dependency_is_readable_in_offline_nonroot_executor`; policy/cache tests bind manifest, image/platform and setup command, reject unsafe inputs and clean failed setup |
| E5 default filesystem/network refusal | `test_repository_executor.RepositoryCommandDockerTests.test_network_and_root_writes_blocked`; `test_sandbox.DockerBoundaryTests.test_host_file_and_key_are_not_available`; `test_network_is_blocked`; `test_root_filesystem_read_only_and_nonroot` |
| E5 explicit credential/profile boundary | `test_execution_profiles.ProfileDockerTests.test_fake_credential_delivered_without_network_mounts_or_output_leakage`; exact-scope, forced-approval, no-host-network, no ambient credentials and output-withholding tests |
| E6 permission decisions | `test_command_policy.CommandPolicyTests`; `test_file_policy.FilePolicyTests`: deny precedes IO/execution, scopes cannot cross executors/projects, generic file rules retain core/path/stale guards, read filtering and guidance/staging authorization |
| E7 live output, cancellation and descendants | `test_command_streaming.DockerStreamingTests.test_live_output_cancel_cleans_container_and_descendants`; `test_live_output_precedes_exit_and_runaway_stream_is_bounded`; host process-group tests and real-PTY journal/prompt recovery |
| E8 jobs and lifecycle cleanup | `test_jobs.JobDockerTests.test_real_docker_job_cancel_removes_container_and_keeps_snapshot`; host cancel/exit/restart tests, real CLI prompt/exit test, lifetime/count/log limits and prepared-closure release test |

The build-artifact test fills a specific previous gap: command tests already
proved real `unittest` execution, but none asserted that a build produced and ran
an artifact inside the container. The rest of the gate reuses existing tests.

## Scope of a passing gate

A pass justifies closing this sandbox milestone for the implemented Docker/Python
execution boundary, including explicitly selected prepared images. It does not
claim arbitrary-language compatibility, unlimited development servers, a security
audit against kernel exploits, or live model coding quality.

The default image is Python-focused. Prepared images must include Python and
`/usr/bin/env` for the bootstrap plus required tools. Candidates retain the
read-only root, non-root user, resource caps and `noexec` temporary filesystem;
new native binaries cannot execute there. Dependency download/network exceptions
require explicit user selection and approval. Credential-bearing command output
is withheld. Jobs are bounded to at most one hour and do not survive a crash.
Deliberately detached host children are not contained by host process-group
cleanup; Docker is the containment boundary. File policies do not retroactively
scrub saved transcripts or constrain separately authorized host command effects.

## Recorded audit: 28 November 2026

Audited base: `95b5350f7134cb070a4109042f74d1ad90d1437e`, plus the build test
and acceptance runner introduced with this document. Host: macOS arm64,
Python 3.14.4. Resolved container image:
`sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b`.

- Boundary gate: **121 tests passed, zero skipped**, in 40.828 seconds.
- Repository command pack: **5/5 passed**; all five known-broken fixtures were
  rejected, and both public and final commands passed after their reference repair.
- Inference calls: **0**.
- Local retained task report:
  `.codine/evals/command-attempts/a1e70d5419ff4a60830cd6429204f197.json` in the audit
  checkout. Reports remain ignored; rerunning produces a new attempt ID.

This evidence satisfies the documented M2 acceptance gate within the scope above;
closing M2 is justified. It does not close the representative-task/model-quality
milestone or establish overall parity with Pi, Claude Code, or Codex.
