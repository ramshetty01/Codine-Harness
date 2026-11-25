# Evaluation execution sandbox

The Docker backend executes candidate Python functions in disposable containers. It is separate from Codine's interactive `run_command` tool, which still runs approved commands with the user's permissions.

## Run it without model calls

Docker must be running and a trusted Python image must already be installed locally. This milestone used the existing `python:3.12-slim` image; the runner never pulls an image automatically.

```bash
.venv/bin/python -m codine.evals --offline --executor docker --report .codine/evals/docker.json
CODINE_DOCKER_TESTS=1 .venv/bin/python -m unittest discover -s tests -v
```

`--image IMAGE` selects another locally installed compatible Python image. The tag resolves to a content-addressed image ID once per run, and the report records that ID. This pins the image for reproducibility; it does not authenticate its publisher. Trust the selected image and Docker daemon/context.

If Docker is unavailable, the requested Docker run fails. It never silently executes the candidate on the host. `--executor local` remains the default only for authored, trusted offline fixtures. Use `--executor docker` for isolated candidate execution.

## Boundary and data flow

```text
Host: task prompt + independent expected answers
  │
  ├─ candidate source + input arguments only
  │          ↓ stdin
  │   Disposable Python container
  │          ↓ bounded JSON results
  └─ host compares actual values against expected answers
```

No host directories, Docker socket, session files, API keys, reference solution, or expected-answer file are mounted into the container. Standard output from candidate code is redirected to diagnostic stderr; the wrapper emits one JSON result. Extra/malformed output, empty exit-zero output, nonzero exit, timeout, or output overflow cannot count as a passing result.

The grader accepts returned JSON values and performs comparisons outside the candidate process. A candidate still knows its inputs and can attempt to game a behavioral benchmark. This design is not cryptographic proof that a solution generalizes; holdout cases and review remain necessary.

## Enforced execution settings

- Network disabled (`--network none`).
- Read-only container root filesystem.
- User/group 65534, all Linux capabilities dropped, and no-new-privileges enabled.
- Maximum 32 processes, 128 MiB memory with no additional swap allowance, and one CPU quota.
- Writable scratch space limited to a 16 MiB tmpfs at `/tmp`, with noexec/nosuid.
- Input payload limited to 256 KB; combined captured output limited to 64 KiB by default.
- Default candidate wall-time allowance of five seconds, including container creation; Docker control and cleanup operations have separate bounded timeouts.
- Docker log storage disabled for these containers.
- A unique container name per execution; removal after normal completion, timeout, overflow, or interruption. Cleanup failure is reported rather than silently ignored.

These settings use Docker's documented container resource, filesystem, user, and network controls. [Docker run reference](https://docs.docker.com/engine/containers/run/)

Only the container owned by that execution is removed. No global pruning or cleanup of other containers occurs.

## What the tests establish

The opt-in integration tests run real containers and verify:

1. A valid candidate returns the expected values.
2. A host-only sentinel file and injected host API-key variables are unavailable.
3. An outbound connection attempt fails.
4. A write outside scratch storage fails and the process has non-root UID.
5. A hanging candidate times out; excessive output stops execution.
6. KeyboardInterrupt removes the created container.
7. Exiting zero without a valid result is rejected.

Unit checks also verify the configured restrictions, absence of host mounts, failure when Docker is missing, and independent host-side grading. The full 12-task scripted suite can use this backend; it still measures infrastructure rather than model quality.

## Remaining limits

This backend runs one Python source file exposing `solve`, not arbitrary repository build commands. Future multi-file evaluations need a controlled staging/copy policy, dependency setup, and immutable grader separation. Do not treat the existing interactive shell tool as sandboxed.

Docker isolation relies on a trusted, maintained daemon and its OS/kernel boundary. It is not a guarantee against container/kernel exploits. Test fixtures do not prove resistance to every hostile program. This backend does not automatically enable API evaluations or change the $5 spending plan.
