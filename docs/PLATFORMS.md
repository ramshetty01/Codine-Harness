# Maintained platform policy

Codine supports **macOS and Linux with Python 3.11–3.14**. The CI gate currently runs all eight combinations of macOS 15 / Ubuntu 24.04 and those four Python versions. Each job checks unit/CLI/PTY behavior and a separately installed wheel outside the checkout. A Linux Docker job verifies container boundaries and repository workloads. A new release must keep these jobs passing; an OS or interpreter version is not claimed supported merely because installation succeeds.

Python newer than 3.14 may run but is not yet in the maintained test matrix. Python older than 3.11 is rejected. Alternate Linux distributions/macOS releases and CPU architectures are not individually certified by this matrix. Docker workloads additionally require a working Docker daemon, an appropriate image for the workload, and permissions to access it. Host mode remains a distinct, explicitly authorized execution environment.

**Native Windows is unsupported.** The harness uses POSIX advisory file locks, process groups, descriptor-relative file operations, and signals. The console entry point and `python -m codine` check platform compatibility before importing the POSIX runtime and return a clear error with exit status 2. This is a maintained support policy, not a claim that the Python internals have been ported.

**WSL2 is experimental, not a tested release target.** Linux Python inside WSL is admitted by the Linux platform guard, but WSL filesystem semantics, Windows-mounted workspaces, terminal behavior and Docker integration have no dedicated acceptance job. Do not interpret admission as validation. Windows Python remains unsupported even when started from a WSL-related terminal.

Support changes require a dedicated CI target and evidence for install/startup, file locks and crash recovery, interrupt/descendant cleanup, terminal input/resize, descriptor/path protections, and installed-wheel restart. Tests must cover native behavior rather than suppress POSIX failures. Until then, no automatic fallback replaces process isolation, locking, or stale-file checks.

Recovery: on an unsupported platform, no CLI configuration, session, or provider initialization is performed. Use an environment from the supported matrix; keep existing project/session files intact. See [Installation](INSTALLATION.md) and [Recovery](../RECOVERY.md).
