# Milestone 5: project customization acceptance

This gate checks Codine's local customization mechanics without paid inference.
It does not score model obedience to project guidance or claim parity with another
coding assistant.

## What is verified

| Capability | Evidence |
| --- | --- |
| Scoped project guidance and settings | `test_instruction_hierarchy`, `test_settings`, `test_settings_integration`: precedence, source freshness, protected configuration, redaction and authoritative budgets |
| Explicit prompt templates | `test_prompt_templates`: scoped discovery, literal substitution, preview and unsent draft; no implicit command or attachment execution |
| Progressive skill loading | `test_skills`: metadata discovery, explicit instruction/reference loading, ignore/private-path checks and session authority reset |
| Reviewed project facts | `test_project_memory`, `test_memory_policy`: proposals stay inactive until user approval; compaction/resume cannot grant approval; deletion stops active injection while historical records remain |
| Executable trust | `test_extension_trust`, `test_extension_process`, `test_extension_manager`: exact project-bound snapshots, explicit grant, staged bytes, no untrusted import, revocation checks before invocation |
| Extension lifecycle | `test_extension_lifecycle`, `test_extension_commands`: real worker load, tool/command/panel invocation, failed replacement preservation, new grant/reload, unload and idle/plan boundaries |
| Failure isolation | `test_extension_process`, `test_extension_manager`: bounded protocol/output, timeout, cancellation, process-group cleanup, invalid registration, callback failure after durable completion |
| Installed interface | `scripts/check_install.py`: installed wheel outside the checkout loads, invokes, reloads and closes a trusted local extension fixture |

All four version-one registration surfaces are exercised: tools, commands,
`turn_end` events and plain-text panels. The runtime validates descriptors again
before changing the parent registry. A replacement must pass trust, startup and
registration validation before old registrations are removed.

Model-selected extension tools require parent approval even in auto-edit mode.
Planning mode prevents extension loading and execution. Explicit user commands
and event callbacks rely on the exact snapshot's prior trust grant. Revocation
blocks subsequent invocations; it does not undo previous effects.

## Limits and recovery

Trusted extensions execute with host-user privileges, not an OS sandbox. Minimal
inherited environment, staged source and child supervision reduce accidental
coupling; they do not prevent trusted code from reading host files, using the
network or creating deliberately detached processes. See [EXTENSION_WORKERS.md](EXTENSION_WORKERS.md).

An existing worker retains approved staged bytes when source files change. Loading
the new source requires another review and grant. A failed reload retains the old
worker; a failed invocation retires the affected worker without replay. Unload
removes its registrations. Exit/restart unloads all extensions; resume does not
automatically load or replay them.

Completion hooks are best-effort notifications after durable successful turns.
A crash between saving and invoking a hook may omit the notification. Hook errors
cannot certify, retry or rewrite the saved coding outcome. Session archives and
summaries can retain deleted facts; memory deletion removes only active memory
injection, as documented in [MEMORY_POLICY.md](MEMORY_POLICY.md).

## Running the gate

Use the repository's full offline test suite, including Docker boundary tests,
and the installed-wheel check. The exact integration commit, test totals and
hosted platform run are recorded in [IMPLEMENTATION_PROGRESS.md](IMPLEMENTATION_PROGRESS.md).
No real provider request is necessary for this milestone. The remaining long-task
summary-quality and live coding evaluation gates are tracked separately.
