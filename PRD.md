# Codine terminal coding harness product requirements

Version 1.1 — 22 November 2026 (implementation and reader-review clarifications)

## 1. Purpose and outcome

Codine is a Python terminal coding agent built so Ram can understand, modify, and use an agent harness. This release should turn the current educational script into a useful project assistant: give it one complete prompt, let it inspect relevant code, review proposed changes, execute approved checks, and continue the same work after a restart.

The model proposes actions. Python controls which actions exist, validates their arguments, executes them, and records the outcomes. The terminal is an interface to that process. None of these components gives the model unrestricted magical access to the computer.

This PRD is both a product specification and a guide to the implementation. The target is a Pi-inspired Python MVP, not a copy of Pi, its branding, or its TypeScript source. NVIDIA Nemotron remains the default backend because the current project has working NVIDIA access. Existing OpenRouter experiments are historical material, not a requirement to switch providers again.

### Definition of good enough

The release is ready when a person can paste a multiline implementation request, let Codine inspect every relevant source page, approve a new file or edit, run an approved check, stop, resume the same session, and restart into modified harness code without losing a valid conversation. Automated tests must cover failure paths, not only happy-path demos. A live model smoke test and a real terminal test supplement those tests.

It is not ready merely because the program parses or the model says it works. Actual tool outcomes and saved state are the evidence.

## 2. What the current failures teach us

| Observed failure | Cause | Requirement |
|---|---|---|
| Long pasted prompts arrive as fragments | Repeated `input()` consumes one line at a time | A paste-aware editor plus explicit multiline entry |
| The model cannot find the end of main.py | Reads stop at 12,000 characters | Paginated reads with an explicit next-page cursor |
| A tool exists in Python but the model denies having it | Schema and dispatch drift, or running process predates edits | One registry supplies schemas and dispatch; restart loads code |
| An edit appears on disk but the running tool does not change | Python objects in memory remain the old version | A real process restart with session restoration |
| The agent offers instructions instead of acting | Incomplete tools and incomplete source visibility | Read, search, write, edit, check, and execution tools |
| A saved conversation might contain unmatched tool calls | Save occurs between assistant calls and tool results | Durable action journal and valid request projection |
| main.py was deleted | A deletion function could target core files | No built-in delete tool; protected infrastructure paths |
| The model invents implementation progress | Prose is being treated as evidence | Tool-event reporting and explicit outcome records |

The current uncommitted source will be preserved in `.codine/backups/pre-mvp/` before restructuring. The user's unrelated `example.py` changes stay intact. `.codine` is local and ignored by Git.

## 3. Pi reference and deliberate differences

Primary sources were checked on 21 November 2026. The former `badlogic/pi-mono` README now points to the `earendil-works/pi` repository. These links identify the reference; feature availability can change after this date.

### Pi as a small extensible harness

Pi describes a minimal extensible harness with interactive, print/JSON, RPC, and SDK entry points. Extensions, skills, templates, and themes adapt behavior. Codine adopts the separation between reusable agent behavior and its terminal interface; it does not implement Pi's package ecosystem in this release. [Pi README](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/README.md)

### Agent state and model messages

Pi's agent core distinguishes application-level messages from model-facing messages, and exposes events around requests and tool execution. Context transformation happens before a provider request. This suggests a useful boundary for Codine: keep canonical saved history separate from the bounded history sent to NVIDIA. [Pi agent core](https://github.com/earendil-works/pi/blob/main/packages/agent/README.md)

### Terminal behavior

Pi documents a prompt editor, multiline input, visible tool events, model/session information, and commands for continuing work. It also supports steering while work is running. Codine adopts the editor and status concepts, but queues and concurrent steering are deferred. Its first release processes one task at a time and uses Ctrl+C to interrupt. [Pi terminal usage](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/usage.md)

### Sessions and context

Pi saves sessions, associates them with working directories, and supports branching, resuming, and compaction. Codine initially stores a linear session with an action journal. It rejects loading a session for a different workspace. Branching and visual history navigation are future work. [Pi sessions](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/sessions.md)

### Compaction

Pi can summarize older history while retaining recent messages and preserving the original entries. Tool results must remain associated with their calls. Codine's first context policy is simpler: keep complete recent user-turn groups and disclose when older groups are excluded. It does not claim that deterministic trimming is an intelligent summary. Full history remains on disk. [Pi compaction reference](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/compaction.md)

### Extensions and reload

Pi extensions register tools and commands, receive lifecycle events, and can be reloaded from supported locations. They execute with the process's permissions. Codine adopts a registry with colocated schema and implementation. Dynamic third-party plugins are deferred; `/restart` reloads the Python application after checks. [Pi extensions](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/extensions.md)

### SDK boundary

Pi's SDK exposes a session owning tools, model, messages, resources, and runtime state. Codine's `Agent` similarly owns a session and receives a provider and registry explicitly. It is ordinary Python that can be tested with a fake provider, rather than code tied to a live API or terminal. [Pi SDK](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/sdk.md)

### Decisions specific to Codine

- Default approvals stay on for file mutations and command execution. This is our product decision, not a claim about Pi's default behavior.
- An explicit auto-edit mode may skip ordinary project-file edit prompts; core harness edits and commands still require approval.
- A command subprocess is not a sandbox. A working directory restricts where it starts, not what it can access.
- No deletion tool is advertised. Git recovery is documented and never automatically destroys uncommitted work.
- NVIDIA is the only live provider required for the MVP. No unsupported cross-provider free-model promise is made.

## 4. Users, tasks, and non-goals

The primary user is learning Python while building an assistant for local projects. They need understandable code, visible state transitions, short feedback, and recovery when experimentation breaks the application.

Core jobs:

1. Ask the agent to explain and change real project code.
2. Understand which tool executed, what it returned, and why another model call was needed.
3. Save work and resume without repeating a long prompt.
4. Develop the harness using its own tools, then deliberately restart into the new version.
5. Inspect a diff and actual test output before trusting a change.

Not included in this release: full Pi feature parity, OAuth, billing guarantees, subagents, MCP, web browsing, remote deployment, GitHub publishing tools, image input UI, plugin hot reload, a sandbox, a full-screen transcript renderer, or an autonomous package installer. Models and free quotas are external dependencies. A poor model response remains possible even when the harness is correct.

## 5. Product requirements and acceptance criteria

### R1 Terminal input

- The normal editor supports bracketed paste. Pasting several lines becomes one editable prompt.
- Enter sends a prompt; Alt+Enter inserts a newline. `/paste` provides a fallback: collect lines until a line containing only `/end`.
- Piped stdin is one prompt, not a sequence of accidental approvals.
- Noninteractive execution denies actions requiring confirmation unless an explicit policy authorizes the action. There is no blanket automatic shell approval.
- Commands are intercepted before a model request. `/help` and `/clear` must not incur an API call.
- Ctrl+C at the editor cancels input; EOF exits. Ctrl+C during a request stops the turn and returns control.

Acceptance: a terminal test pastes a three-line task and the fake provider receives a single user message containing all three lines. A subsequent approval must not consume another line from that task.

### R2 Project selection and file visibility

- `--project PATH` chooses the workspace; default is the current directory.
- The application directory and project directory are distinct. Running Codine against another project must not silently use Codine's source directory as the tool root.
- Listing and searching work recursively in the workspace while excluding Git internals, credentials, sessions, virtual environments, and common generated directories.
- Reads support text/code/docs in nested directories, with line numbers and continuation cursors. A model can inspect README.md and the end of a large Python module.
- Binary files are rejected. Reads and search results have explicit output bounds.
- Paths are relative; absolute paths, parent traversal, and symlink escapes are rejected by file tools.

Acceptance: read every page of a source larger than the old limit, retrieve a final marker, read a nested Markdown file, and reject `.env`, `.git/config`, `../outside`, and symlink targets.

### R3 Registry and validation

Every tool has one `ToolSpec` containing its name, description, input schema, and callable. The model-facing `tools` array and dispatcher derive from that registry. Adding a tool requires one registration plus tests, not two distant edits.

Arguments must be a JSON object. Validate required keys, unexpected keys, and JSON types before calling Python. A malformed request becomes a tool error, not a process crash. Unknown tool names are never evaluated as Python code.

Acceptance: registration generates a schema and dispatches the matching handler; missing/extra/wrong-type arguments are rejected; a new test tool can be registered without editing the agent loop.

### R4 File mutation

- `write_file` creates a new project-relative text file and can create its parent directories after approval. Existing files cannot be silently overwritten.
- `edit_file` replaces exactly one occurrence. It requires a prior read of the current file version, preventing a model from editing unseen or stale contents.
- Both tools show a unified diff and require approval by default.
- Re-read/check file identity after approval. If the user edited it meanwhile, refuse and ask the model to read again.
- Save via a temporary file in the destination directory and atomic replace. This prevents a partially written file from appearing after interruption; it is not a full transaction across multiple files.
- Preserve file permissions on replacement. Reject mutating hard-linked or symlinked files.
- Core code changes always require approval, even in auto-edit mode. No direct delete tool is included.

Acceptance: decline changes nothing; approve writes exactly the preview; an externally modified file is not overwritten; existing new-file targets are refused; core-file edits do not bypass approval.

### R5 Execution and verification

- `check_python` parses a project Python file and reports a line-numbered syntax error without executing it.
- `run_command` accepts an argument array, not an interpolated shell string. Show the exact command, working directory, and timeout before approval.
- Spawn with `shell=False`, stdin disconnected, bounded timeout, and bounded output. On timeout/interruption/output overflow, terminate the child process group where supported.
- Return exit code, stdout, stderr, and timeout/truncation information as a tool result.
- The child environment omits configured provider keys. This does not prevent approved code from reading local credential files: it is not isolation.
- A successful process exit and a successful model answer are separate facts. Never infer test success from prose.

Acceptance: execute a harmless Python print; capture a nonzero exit with traceback; kill a hanging child; handle large output without hanging; deny execution without approval. Tests use temporary projects and harmless subprocesses.

### R6 NVIDIA provider and streaming

Use the existing NVIDIA endpoint and model. Keep provider-specific request construction outside the agent loop. Preserve streaming text, collect reasoning separately, and assemble fragmented tool requests by index.

Only execute complete tool batches after a terminal finish marker and valid arguments. A missing marker or length-truncated tool request must not trigger an action. Support multiple tool calls in one model response but execute local actions sequentially.

Reasoning display is off by default to keep the transcript usable; `/thinking` toggles display, not permissions. Reasoning metadata remains available where NVIDIA needs it. Show reported usage and request duration after completion. Do not invent token counts or cost when absent.

Acceptance: fragmented names/arguments form the correct call; two interleaved calls retain their IDs; usage-only final chunks work; truncated streams never execute tools.

### R7 Persistent sessions and crash semantics

Session files live in ignored `.codine/sessions/` inside the workspace. A session includes format version, ID, workspace, model, timestamps, messages, usage totals, active-context boundary, and an optional pending tool batch. No API key is part of the config serialized into a session.

Write snapshots atomically with restrictive permissions. Flush and fsync the temporary file before replacement, then fsync its parent directory. Snapshots are limited to 20 MB on load; long-lived sessions need a new-session handoff before reaching that size. Hold an advisory session lock so two processes cannot write the same session simultaneously. Malformed JSON, unsupported versions, invalid roles, mismatched tool IDs, and workspace mismatch must be reported rather than silently discarded.

A tool call and its result form one protocol relationship. Saving only an assistant tool request and later treating it as complete history is incorrect. Use this sequence:

1. Save the user message.
2. Receive and validate the assistant response.
3. If it requests tools, save an **intent journal** outside finalized messages.
4. Execute a tool, record its result in the journal, and save.
5. Once every call has a result, append the assistant request and results together to finalized history; clear the journal and save.
6. On resume after a crash, keep recorded results and synthesize an interrupted/unknown-outcome result for each unfinished action. Never replay it automatically.

Ctrl+C during a tool batch follows the same repair rule before accepting another prompt. If saving or repair fails, stop further actions and exit with an error. Session locks are acquired before reading a snapshot and are not inherited across exec.

An OS crash can happen after a file write but before its result is recorded. Exactly-once external execution is not promised. The recovery message must say the outcome is unknown and instruct inspection, avoiding accidental double execution.

Known configured secrets are redacted before persistence/output. This is a defense, not a claim that arbitrary user-pasted secrets can always be detected. Sessions may contain private source and prompts and must not be published automatically.

Acceptance: save/resume preserves matching IDs; corrupted sessions fail safely; a pending batch resumes as valid history without invoking a handler; two writers conflict; a failed save stops further actions.

### R8 Restart and self-development

`/restart` must perform an actual process replacement, not clear a Python list. It saves the current session, validates owned Python sources, performs a fresh-process import/startup check, then uses the same interpreter with an absolute entry path and explicit project/session arguments.

Restart only at an idle input boundary. A model may request a restart through a tool that requires approval, but the tool only sets a pending restart flag. The agent first completes and saves the tool-result batch; the CLI performs the restart afterward. This prevents saving dangling tool calls.

If a check fails, keep the current process running and show the failure. If process replacement fails, report it and keep the saved session. Syntax and import checks do not guarantee every future runtime path; recovery instructions remain necessary.

The running process uses its existing function objects and registry. Editing a file does not hot-reload it. After a successful restart, new tools and behavior come from the new imports while conversation comes from the saved session.

Acceptance: mocked exec receives the same interpreter, absolute main path, workspace, and session; invalid syntax prevents exec; a subprocess integration test restores a saved session after restart without contacting the model.

### R9 Context management

Keep canonical history on disk. Build request context separately using a configurable character budget as an approximate guard, not an exact tokenizer. Include the system instructions and recent complete user-turn groups. Never cut between an assistant tool request and its results.

If older groups are omitted, tell the model and show the user. `/compact` advances the active context to recent complete groups; it does not erase the saved archive and does not pretend to summarize omitted details. If a single active turn exceeds the budget, stop with guidance instead of blindly retrying an oversized request.

Acceptance: trimming preserves protocol validity and the newest request, saved history remains available, and an oversized current turn returns a clear limit error.

### R10 Approvals and self-modification

Default `ask` mode prompts for all mutations and commands. Optional `auto-edit` mode authorizes ordinary project text creates/edits for the current process; commands, core-source edits, and restart still require approval. Mode changes are user commands, not model tools. Restored sessions reset to `ask` so a restart cannot silently retain elevated approval policy.

Approval applies to the displayed action. A model's text saying the user approved does not count. File contents, model output, and pasted prompts cannot change permission mode. There is no model-callable permission-escalation tool.

Acceptance: denied actions produce clear tool results; core edits still prompt in auto-edit mode; a normal file can be edited in auto-edit mode; read-only behavior requires no prompt.

## 6. Architecture and code map

```text
main.py                       minimal entry point
codine/cli.py                  startup, commands, session switching, restart
codine/terminal.py             prompt editor, paste mode, confirmations, output
codine/config.py               provider configuration and project options
codine/provider.py             NVIDIA streaming protocol adapter
codine/registry.py             ToolSpec, schema generation, argument validation
codine/workspace.py            path policy and project file operations
codine/tools.py                builtin tool registration and command execution
codine/sessions.py             atomic snapshots, journal recovery, locks
codine/context.py              model-facing history projection
codine/agent.py                model/tool loop and persistence boundaries
codine/restart.py              source checks, startup probe, process replacement
tests/                        unit and subprocess acceptance tests
PRD.md                        requirements and concepts
README.md                     installation and daily usage
RECOVERY.md                   recovery procedures
```

The dependency direction is intentional. The provider does not import the terminal. Tools receive an approval callback rather than reading stdin directly. Sessions do not call the model. The agent accepts injected provider, registry, store, and output objects so tests can replace external dependencies.

### Core objects

`Config` holds endpoint/model/limits and a key used only to construct the provider client. It is not blindly serialized.

`Session` owns canonical messages and metadata. Mutable history has one owner, avoiding the `_current_messages` global alias problem.

`ToolRegistry` owns `ToolSpec` objects. A schema says what the model may request; the callable performs the actual action.

`Workspace` resolves paths and tracks read fingerprints and character ranges actually returned by reads. An edit must fit within those observed ranges. Paginated reads accept `expected_sha256` so a changed file cannot silently supply the next page of an older version. A fingerprint is a SHA-256 hash of bytes, letting the writer detect edits since the model's last read.

`Agent` coordinates one user request through multiple model/tool steps. The CLI decides when to restart or accept another prompt.

### Example registry code

This deliberately minimal teaching example shows only the required path argument. The actual schema in `codine/tools.py` also declares `start_line`, `max_lines`, `start_char`, and `expected_sha256` for pagination.

```python
registry.register(ToolSpec(
    name="read_file",
    description="Read a page of a project text file.",
    parameters={
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
        "additionalProperties": False,
    },
    handler=workspace.read_file,
))
```

The schema and handler sit together. The registry builds the API declaration and validates the same arguments before calling `handler(**arguments)`. `**arguments` unpacks dictionary keys into named Python parameters.

### Agent algorithm

```python
append_user_and_save()
for step in range(max_steps):
    context = project_context_without_splitting_tool_pairs()
    reply = provider.complete(context, registry.schemas())
    if reply.has_tools:
        begin_durable_tool_batch(reply)
        for call in reply.tool_calls:
            result = registry.execute(call)
            record_result_and_save(call.id, result)
        finalize_batch_and_save()
        if restart_requested:
            return
    else:
        append_answer_and_save(reply)
        return
report_step_limit()
```

A step is one API request plus its tool batch. A user's task may require several steps. `max_steps` bounds runaway loops, but a step limit is not the same as a dollar budget or total wall-clock timeout.

## 7. Concepts explained through this project

### Source files versus running objects

Disk stores text. Importing executes module definitions and creates objects in memory. Changing the text does not mutate those objects. Restart creates a new process image that imports the new source. Session restoration reconstructs conversation data, not function objects.

### JSON versus Python values

The model sends JSON arguments as text. `json.loads()` turns that into a dictionary. Validate it, then dispatch to a known callable. Never use `eval()` to interpret a tool request.

### Streaming versus complete messages

A stream is a sequence of deltas, not a sequence of complete answers. Text fragments can print immediately. A tool name or JSON argument may be split mid-word or mid-string, so execution waits until assembly and validation finish. A usage-only chunk can have no choices.

### State versus presentation

A progress line such as “Reading file…” belongs in the UI. The actual file result belongs in history. Logs and status banners must not accidentally become user instructions to the model.

### Atomicity versus transactions

`os.replace()` makes a completed temporary file replace a destination in one filesystem operation. It avoids half-written JSON being visible. It cannot make a subprocess and a session save happen as one atomic transaction. The pending-action journal handles that gap honestly.

### Persistent memory versus context

The session archive can grow while model context is limited. A saved message is not necessarily included in the next request. Context projection selects what is sent, while history remains available for explicit resume/inspection. Clearing context does not undo edits.

### Parsing versus testing

`ast.parse()` proves that text follows Python grammar. Import checking catches missing modules and import-time failures. Unit tests check selected behavior. A live request checks integration with the provider. None alone proves the whole system correct.

### Permissions versus isolation

An approval prompt asks whether an action is authorized. A sandbox limits what an action can do. Codine provides approval and path policies for its file tools, not an operating-system sandbox. Approved Python code can still access the user's machine.

## 8. Terminal commands

| Command | Behavior |
|---|---|
| `/help` | List commands and keybindings |
| `/paste` | Collect lines through `/end` as one prompt |
| `/session` | Show ID, workspace, message count, usage, and save path |
| `/sessions` | List saved sessions |
| `/resume ID` | Save current state and open a validated session |
| `/new` | Start a new session, preserving the previous one |
| `/save` | Save current state explicitly |
| `/clear` | Advance the active-context boundary; preserve the complete disk archive |
| `/compact` | Keep recent complete turns in active context; retain archive |
| `/restart` | Save, validate, and replace process; resume automatically |
| `/model [ID]` | Show or set NVIDIA model for the session; no false compatibility promise |
| `/thinking` | Toggle reasoning display |
| `/permissions ask\|auto-edit` | User-selected edit policy; commands remain confirmed |
| `exit`, `/quit` | Save and exit |

CLI flags should include `--project`, `--resume`, `--continue`, `--prompt`, and `--check`. `--check` validates local startup without requiring a credential, acquiring a session lock, writing a session, or contacting a model. A piped prompt runs once and exits.

## 9. Security, privacy, and recovery details

Project path checks apply to built-in file tools. Reject hidden credential/runtime directories even when a model requests them. A secret masker replaces configured API-key strings in displayed and persisted data; it cannot identify all possible secrets. Session files contain private working context and remain ignored.

Core source is editable with explicit approval, enabling self-development. Core paths are application `main.py`, every path under application `codine/`, and application `requirements.txt`, `.gitignore`, `RECOVERY.md`, and `PRD.md`. Select the application repository as the workspace to develop Codine itself. No direct registry-removal or deletion tool is advertised. Approved code edits can nevertheless change or break those functions, and approved commands can delete files. The absence of a delete tool is not an absolute protection. Changing permission mode through model arguments and automatic untrusted-plugin loading are unsupported. An approved command can do more than file tools; the confirmation text must say it executes locally without sandbox isolation.

Git is the recovery boundary. Preserve a known working commit before a new milestone. Never run `git reset --hard`, restore all files, or pop a stash automatically. Recovery examples restore specific paths and explain that local changes to those paths are replaced. PRD and recovery documentation must be readable through the agent's normal file tools.

## 10. Implementation milestones

1. **PRD and baseline**: record reference links, requirements, acceptance matrix, and a local backup of existing changes. Commit the PRD first.
2. **Core boundaries and file tools**: modules, registry, pagination, text files, stale-edit checks, new files, safe confirmation UI.
3. **Sessions and agent loop**: snapshots, locks, interrupted-action repair, canonical history, bounded context, provider adapter.
4. **Terminal and restart**: paste-aware editor, slash commands, status, resume, checked exec, print mode.
5. **Execution and hardening**: approved bounded subprocesses, tests for nonzero/timeout/cancel/output cap, self-development acceptance workflow.
6. **Documentation and verification**: update README/RECOVERY and a test report with actual outcomes. Fix discovered defects and repeat the relevant checks.

Each commit should describe working behavior. Do not publish secrets or local sessions. Keep unrelated user sample edits out of implementation commits.

## 11. Acceptance matrix

| ID | Scenario | Expected proof |
|---|---|---|
| A01 | Multiline paste | One user message in mocked/PTY test |
| A02 | Read beyond old cap | Last-page marker retrieved with cursor |
| A03 | Nested README/search | Correct path/line text returned |
| A04 | Forbidden/symlink paths | Clear refusal; outside bytes unchanged |
| A05 | Edit yes/no/stale | Approved exact change; rejected cases unchanged |
| A06 | New file | Created only after approval; overwrite refused |
| A07 | Schema/dispatch | Registry test rejects invalid args |
| A08 | Streaming tools | Interleaved fragments assembled; truncated calls rejected |
| A09 | Save/resume | IDs and results preserved; no API key serialized |
| A10 | Interrupted batch | Synthetic outcome for missing result; no action replay |
| A11 | Session corruption/lock | Safe error; original file preserved |
| A12 | Restart | Same interpreter/project/session; invalid code prevents exec |
| A13 | Context | No orphan tool results; archive retained |
| A14 | Command success/error | Actual stdout/stderr/returncode captured |
| A15 | Command timeout/output | Process group stopped; bounded result |
| A16 | Self-development | Agent can read, edit, check, and request a restart |
| A17 | Live NVIDIA | At least one real text/tool round trip, or precise external blocker |
| A18 | Repository hygiene | Clean committed implementation; ignored secrets/sessions; unrelated sample changes preserved |

## 12. Definition of done and known limits

A good-enough release meets the matrix with reproducible commands and records live-test limitations. If A17 is blocked by external provider access, label the result “implementation complete, live acceptance pending” rather than declaring full acceptance. The verified release records an actual passing live workflow. It is usable for small local coding tasks and explains its own boundaries. It does not claim equivalence to the full Pi or Codex product.

Known MVP limits include approximate context sizing, linear sessions, manual recovery after bad runtime logic, local rather than sandboxed command execution, a single active task, no image UI, and provider/model variability. If an advertised feature is deferred during implementation, update this PRD and the final report instead of silently calling it complete.

## 13. Suggested learning order

1. Read `main.py` and follow the call into the CLI.
2. Read `registry.py`; connect schema, validation, and dispatch.
3. Read `workspace.py`; examine path resolution and stale-write detection.
4. Read `provider.py`; follow one text delta and one fragmented tool request.
5. Read `agent.py`; find every persistence boundary.
6. Read `sessions.py`; simulate an interrupted action and inspect recovery.
7. Read `terminal.py` and `restart.py`; distinguish user input, approval, and code reload.
8. Run tests in temporary projects before giving the agent a larger self-modification task.

The next learning exercise after this MVP is a small extra tool registered through `ToolSpec`, tested without a model, then loaded by a checked restart. That demonstrates extensibility without introducing a plugin system prematurely.
