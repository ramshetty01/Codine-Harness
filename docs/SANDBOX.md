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

This backend supports single-file functions and explicitly staged Python packages exposing `solve`, not arbitrary repository build commands. Dependency setup and larger build workflows remain deferred. Do not treat the existing interactive shell tool as sandboxed.

Docker isolation relies on a trusted, maintained daemon and its OS/kernel boundary. It is not a guarantee against container/kernel exploits. Test fixtures do not prove resistance to every hostile program. This backend does not automatically enable API evaluations or change the $5 spending plan.

## Multi-file Python exercises

Run the repository suite without API requests:

```bash
.venv/bin/python -m codine.evals --offline --executor docker --suite repository --report .codine/evals/repository.json
```

The two development fixtures exercise invoice totals and word counting. Each has a package import and two modules to repair. The scripted provider reads every manifest file, edits the two modules, checks syntax, and finishes; the independent grader then evaluates behavior in a fresh container. Broken fixtures must fail before the reference edits can count as passing.

A manifest is an explicit mapping from relative Python paths to source text. Only its listed files are snapshotted after edits. The staging validator rejects absolute/non-normalized paths, dot directories, symlinks (including parent directories), missing/special files, non-Python files, more than 32 files, and more than 200 KB of source. The existing 256 KB serialized payload cap also applies. No repository directory is mounted. Files are materialized under `/tmp/project` in the container; that path is added to Python's import search path so package imports work. Expected outputs remain outside the container.

This is a snapshot of a controlled, disposable workspace after synchronous tool execution. It is not a race-proof filesystem copier for directories concurrently modified by hostile host processes. It does not install dependencies, run arbitrary build commands, stage data files, or evaluate large repositories. Only standard-library Python packages exposing `solve` through `task.py` are supported. The repository suite refuses the local executor. New unlisted files are intentionally excluded.

Learning distinction: file staging chooses what enters the container; package loading lets those files import each other; Docker restricts execution; the host grader compares returned values. None of these components makes the scripted provider a real model.

### General repository snapshots (E2)

`codine.staging.snapshot_repository(root, paths)` now provides a reusable staging
boundary for future repository command execution. Pass explicit relative file
names; there is no implicit recursive copy, Git discovery, host mount, or inherited
environment. For example:

```python
from codine.staging import snapshot_repository

manifest = snapshot_repository(project_root, [
    "pyproject.toml", "src/app.py", "tests/test_app.py",
])
print(manifest.digest, manifest.total_bytes)
files = manifest.as_text_files()
```

The frozen manifest owns UTF-8 text, byte lengths, and SHA-256 content hashes.
Its digest covers sorted names, hashes, and sizes, independent of selection order.
Changing a host file afterward cannot change the snapshot. The returned mapping
is a copy. Permissions, ownership, executable flags, and modification times are
not transported; a future executor must choose its own safe materialization mode.

Defaults allow 256 files, 1 MB per file, 8 MB total, and 240 UTF-8 bytes per path.
`StagingLimits` can set tighter application budgets. The extension/name allowlist
covers common source files, tests, text configurations, lockfiles, documentation,
Dockerfiles, and Makefiles. It does not execute these files. NUL-bearing binary
files, invalid UTF-8, unsupported extensions, non-normalized paths, traversal,
control characters, duplicate names, and Unicode/case-colliding names are refused.
Credential filenames/directories (including all `.env*`, even `.env.example`), Git
metadata, Codine sessions/state, dependency folders, and generated/cache folders
are excluded even when explicitly selected. **This is a path policy, not a secret
scanner:** credentials embedded in otherwise allowed source/configuration files
are not detected. Only select reviewed files for a container payload.

On POSIX, each directory is opened relative to its parent descriptor with
`O_NOFOLLOW`. Files are read through a no-follow, nonblocking descriptor after
checking regular-file type, one hard link, and size. Reads are bounded; metadata
is checked again to reject observable concurrent changes. Parent symlink swaps
cannot redirect an already opened directory descriptor. Unsupported platforms
fail closed. The caller chooses the trusted repository root; this is not an
atomic snapshot of an entire tree, and hostile concurrent writes or directory
moves are not supported. Snapshot after tools finish, in a controlled workspace.

The existing Python evaluator now uses this reader while retaining its stricter
32-file/200-KB/Python-only policy and its separate 256-KB serialized input cap.
E2 does **not** enable general Docker commands, dependency installs, network
access, write-back from containers, or sandbox the terminal's host command tool.
Those require separate executor and review policy work.

### Repository test and build commands (E3)

`RepositoryExecutor` runs an explicit argument vector against a staged snapshot:

```python
from codine.staging import snapshot_repository
from codine.repository_executor import RepositoryExecutor

snapshot = snapshot_repository(project_root, ["app.py", "tests/test_app.py"])
result = RepositoryExecutor(timeout=20).execute(
    snapshot,
    ["python", "-m", "unittest", "discover", "-s", "tests", "-v"],
    cwd=".", env={"CI": "1"},
)
print(result["status"], result["exit_code"])
print(result["stdout"], result["stderr"])
```

The command runs under `/tmp/project`; `cwd` must name a directory created by the
manifest. Arguments are passed directly, without an implicit shell. The command
can run a build tool only if that tool is already in the explicitly selected local
image. The default Python image supports standard-library tests and Python build
steps; this does not provide package installation or download dependencies.

The executor inherits Docker's network isolation, read-only root, non-root user,
resource caps, no mounts, fixed local image ID, output bound and timeout cleanup.
Input contains only selected files, argv, cwd and permitted environment settings.
No host environment variables are forwarded. The command environment starts with
fixed `PATH`, `HOME`, `LANG`, `TMPDIR`, plus optional `CI`,
`PYTHONDONTWRITEBYTECODE`, `PYTHONHASHSEED`, `NO_COLOR`, `TERM`, and `TZ` values.
Staged files are private writable text files in the disposable tmpfs; build
artifacts vanish with the container and are not written back to the repository.
Stdin is `/dev/null`, so interactive prompts are unsupported. The temporary
filesystem remains `noexec`: interpreted scripts work, but freshly compiled
native binaries cannot execute there. Builds must fit the existing 16-MB tmpfs
and resource limits; large/native dependency-heavy builds remain unsupported.

Results contain separate bounded stdout/stderr, actual attached-process exit
status, execution status, elapsed seconds, image ID, manifest digest, argv and cwd.
`status="completed"` means the process exited, **not** that tests passed: check
`exit_code == 0` and the intended test assertions. `timeout` and `output_limit`
mean execution was terminated; their exit codes describe the killed Docker client
and must not be treated as candidate success. Logs are untrusted command output.
The executor forcibly removes its uniquely named container in `finally`, including
interrupts; failure to confirm cleanup raises an explicit error.

This execution API has real Docker boundary tests. Its interactive integration
and approval policy are described in E1 below. No fallback to host execution or
paid inference is used.

### Interactive command isolation (E1)

Start `codine --executor docker --image python:3.12-slim` to sandbox the
`run_command` tool. The image must already exist locally. Startup resolves and
records its immutable ID; missing Docker or image fails closed, never switches
to host execution. `/sandbox` displays the effective policy and resolved image.
The welcome text and toolbar also identify host versus Docker execution.

In Docker mode the model must provide an explicit `files` array alongside
`argv`, optional snapshot-relative `cwd`, and a bounded `timeout`. Codine reads
and validates these files **before** asking permission. The approval names the
image, command, cwd, timeout, every selected path/size/SHA-256, and the combined
snapshot digest. Execution uses those exact captured bytes even if a file changes
while approval is pending. Rejected or invalid requests never execute. Planning
mode disables command execution; JSON/noninteractive approval remains denied.

Host mode remains available through `--executor host`, and is the default for
new sessions for compatibility. Its approval explicitly states that the command
runs with your user permissions outside a sandbox. Docker isolates **commands**,
not file read/edit tools: those still operate on the host project with existing
path, diff and approval controls. Neither mode makes an arbitrary repository safe.

Sessions persist executor settings and pinned Docker image. `--resume` and
`/restart` retain them. An explicit `--executor host` overrides them visibly.
Switching sessions inside an existing process refuses different execution settings;
start `codine --resume ID` to restore that session's executor. The image's default
entrypoint is overridden and even the Python bootstrap starts under `env -i`;
image-provided environment values cannot flow into candidate execution.

Prepared custom images must contain `/usr/bin/env` and Python on the fixed PATH
for the bootstrap, in addition to any tools required by the selected command.

Command approval can additionally use explicit user-owned [command policy rules](COMMAND_POLICY.md). Default behavior remains ask; deny rules take precedence, and noninteractive allow rules cannot bypass approval denial.

### Live output and cancellation (E7)

Approved host and Docker commands now stream stdout/stderr while running. The
transport emits `command_output` events with `stream`, `text`, and `final` fields;
`RepositoryExecutor.execute(..., on_output=callback)` also exposes this API to
local callers. Terminal output labels each stream and strips terminal controls.
Both terminal and JSON adapters redact known secrets even when a secret or UTF-8
character is split across read boundaries. Possible secret-prefix suffixes are
held until they can be safely emitted. Child programs must flush their output;
Python is configured unbuffered by default in these command environments.

Streaming does not remove limits. Host output retains and displays at most 12 KB
per stream and stops a process producing over 1 MB total. Docker output uses its
existing combined cap (64 KiB by default). Byte caps apply before decoding; invalid
UTF-8 is replaced. The final tool result still includes bounded output and exit
status, and `/details` can inspect it. Live text is untrusted output, not a command
or proof that tests passed.

Ctrl+C interrupts the active command. The host executor cleans its process group
once, sends TERM then KILL, closes pipes, and propagates the interrupt. It also
cleans same-group background children when the original process exits. This is
not OS containment: a malicious host child that deliberately creates a separate
session can escape that process group. Use Docker for containment. Docker forcibly
removes the entire uniquely named container, including child processes, on timeout,
output overflow, cancellation or callback failure; cleanup errors are explicit.

The existing agent journal records an interrupted action as outcome unknown and
does not replay it. CLI recovery saves that state and returns a usable prompt;
inspect workspace effects before asking to retry. Tests include a real PTY running
a scripted (no-API) agent, interrupted after live output, then using `/session` and
exiting normally. Real Docker tests verify pre-exit streaming, output caps and
container disappearance after interruption with a child process present.
