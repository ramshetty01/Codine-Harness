# Codine-Harness

A Python terminal coding assistant informed by Pi, Claude Code, and Codex, built to be understandable. Codine can inspect a project, propose file changes, run approved checks, save its conversation, and restart into updated harness code.

Read **[PRD.md](PRD.md)** for the detailed architecture, concepts, code walkthrough, and acceptance requirements. **[VERIFICATION.md](VERIFICATION.md)** records what was actually tested. **[RECOVERY.md](RECOVERY.md)** explains how to recover from a broken self-edit.

The next-release design is documented in **[docs/DEVELOPMENT_PLAN.md](docs/DEVELOPMENT_PLAN.md)**. Offline and bounded live evaluation are available: **[docs/EVALUATIONS.md](docs/EVALUATIONS.md)** explains the evaluation runner and the later paid API stage.

## Start

For the shared interactive/evaluation allowance and owner-scoped reconciliation, see **[Shared spending](docs/SHARED_SPENDING.md)**.

For provider failure handling and the limits of automatic retries, see **[Provider failures](docs/PROVIDER_FAILURES.md)**.

For narrowly scoped runtime network and credential grants, see **[Execution profiles](docs/EXECUTION_PROFILES.md)**.

For explicit Docker dependency preparation and reusable image IDs, see **[Prepared environments](docs/PREPARED_ENVIRONMENTS.md)**.

For the automated Python/OS test matrix, see **[CI](docs/CI.md)**.

For a standalone wheel, `codine --version`, upgrades, and uninstall instructions, see **[Installation](docs/INSTALLATION.md)**. The checkout workflow below remains supported.

The CI matrix covers Python 3.11–3.14 on macOS and Linux. Windows is not supported by this version's POSIX locking and process-group code.

```bash
cd /Users/ramshetty/Desktop/ram-code/ramscode/codine
source .venv/bin/activate
python main.py
```

To install the `codine` command for your user:

```bash
.venv/bin/python scripts/install_cli.py
```

Then open any project folder and launch it:

```bash
cd /path/to/your/project
codine
```

The launcher uses this checkout's virtual environment, so you do not need to activate it. It preserves your current directory and forwards all arguments (`codine --continue`, `codine --plan`, `codine --help`). Keep this checkout and its `.venv`; rerun the installer if you move them. On a machine where `~/.local/bin` is not on PATH, add `export PATH="$HOME/.local/bin:$PATH"` to your shell configuration and open a new terminal. Remove `~/.local/bin/codine` to uninstall the launcher.

For a fresh checkout:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

Add your NVIDIA API key to `.env`. **Do not replace an existing `.env` when updating.**

Alternatively, use `codine auth set-key nvidia` or `codine auth login openrouter` to save provider API credentials in a private, unencrypted user-owned file. Existing shell/`.env` keys take precedence. See [authentication, status and logout](docs/AUTHENTICATION.md).

```dotenv
NVIDIA_API_KEY=your-key
NVIDIA_MODEL=nvidia/nemotron-3.5-super-vl-120b-a12b
CODINE_MAX_TOKENS=4096
CODINE_MAX_STEPS=12
CODINE_CONTEXT_CHARS=160000
```

By default, the OpenAI Python SDK sends requests to **NVIDIA**, at `https://integrate.api.nvidia.com/v1`. An OpenAI key is not needed. An OpenRouter adapter is available; select it explicitly rather than changing the NVIDIA endpoint. This installation was evaluated with `qwen/qwen3.7-flash` on 27 November 2026. See [the practical release guide](docs/PRACTICAL_RELEASE.md) for results, costs, and limitations. Existing shell variables override `.env`; existing output-budget settings also override the defaults. The app's `.env` loads first, then the selected project's `.env` supplies values that are still unset.

`python main.py --check` validates syntax and startup without an API request or session write. Offline slash commands work without a key. Model access, quotas, and compatible parameters depend on NVIDIA.

## Use it

The terminal opens with a small `[/] codine` header, project path, and model. A plain `›` prompt sits above a quiet footer showing permission mode, tokens, and model. Colors inherit your terminal theme; amber marks input and review. Normal terminal scrollback is preserved. Session details are available with `/session`.

Type `/` to open the command menu. Use arrow keys to browse and Tab to complete a command. Tool rows show the action and target file. Results distinguish success, declined actions, and failures; successful previews show up to three lines, failures up to eight. `/details` shows the last tool result; large results link to paged snapshots readable with `/artifact ID`. Added diff lines are green, removed lines red, and approvals use an open review margin with every detail wrapped rather than truncated. Narrow windows wrap review details. Set `NO_COLOR=1` for monochrome output.

- **Enter** submits; **Alt+Enter** adds a line. A terminal supporting bracketed paste inserts a multiline paste as one editable prompt.
- If your terminal does not preserve pasted lines, enter `/paste`, paste the task, then put `/end` on its own line.
- Diffs appear before writes. Type **yes** to approve; any other answer declines.
- Ctrl+C interrupts the current input or turn, including approvals. Unsent editor drafts stay in memory; completed actions remain recorded. `exit`, `/quit`, or Ctrl+D at the editor saves and exits.
- Reasoning display is hidden initially. `/thinking` toggles display; it does not change the model's thinking budget.

Try:

```text
Read README.md. Create a new greeting.py that prints "Hello, Codine!".
Check its Python syntax, then run it. Report the actual output.
```

To work on a different project:

```bash
python main.py --project /absolute/path/to/project
python main.py --project /absolute/path/to/project --continue
python main.py --resume SESSION_ID
```

The selected project is the file-tool root. Its `.codine/sessions/` stores its own sessions. The application source can live elsewhere.

For one request:

```bash
python main.py --prompt "List the project files and describe the layout."
printf 'Read README.md.\nExplain the startup flow.\n' | python main.py
```

Piped input is one message. Noninteractive approvals are denied; lines of a piped task are never used as approval answers. `--prompt` can still ask for approval when run from an interactive terminal.

## Commands

| Command | What it does |
|---|---|
| `/help` | Show commands and keybindings |
| `/plan` | Read-only file tools; `/permissions ask` enables mutations again |
| `/instructions [PATH]` | Inspect global/root and scoped instruction provenance |
| `/budget` | Show OpenRouter session costs and pending reservation |
| `/session`, `/sessions [query]` | Show current session / search and resume named sessions |
| `/rename TITLE` | Name the current session |
| `/tasks [clear]` | Inspect persistent task progress or explicitly clear the plan |
| `/context` | Inspect active context, summary coverage and remaining estimates |
| `/artifact ID [OFFSET]` | Read a saved tool-output page without a model request |
| `/sandbox`, `/policy` | Inspect effective command isolation and user command rules |
| `/jobs [ID]`, `/cancel ID` | Inspect background work/logs or request cancellation |
| `/save` | Save immediately; saving is also automatic |
| `/resume [ID]`, `/new` | Open saved history (picker if no ID) / start a new session |
| `/clear` | Remove old turns from active model context, keeping the disk archive |
| `/compact` | Keep the most recent three complete turns in active context |
| `/restart` | Save, validate, launch the same interpreter, and resume |
| `/model [ID\|free]` | Pick an OpenRouter model, filter free routes, or set an explicit ID |
| `/thinking` | Toggle reasoning display |
| `/details` | Show the full output from the most recent tool |
| `/permissions ask` | Require approval for every file mutation |
| `/permissions auto-edit` | Approve ordinary file edits; core edits still ask and commands follow user policy |
| `/paste` | Collect lines until `/end` |
| `exit`, `/quit` | Save and exit |

OpenRouter `/model` opens a model picker with advertised prices and capabilities; `/model free` filters zero-priced routes. `/model ID` does not promise compatibility with every model. NVIDIA retains its Nemotron settings; OpenRouter uses complete non-streaming messages to preserve opaque reasoning metadata. Session provider identity is saved; start a new session to change providers.

## Tools and limits

| Tool | Behavior |
|---|---|
| `list_files` | Recursive, filtered listing; narrow the directory when truncated |
| `read_file` | UTF-8 text, line numbers, SHA256, and a next-page cursor |
| `search` | Literal text search with file/line results |
| `write_file` | Create a new text file; never overwrite an existing one |
| `edit_file` | Replace one unique exact occurrence in a region already read |
| `replace_lines` | Replace a previously read line range with an expected SHA256 |
| `check_python` | Parse syntax without executing |
| `update_plan` | Save revisioned task progress without changing project files |
| `read_artifact` | Page historical output and check file-source freshness |
| `run_command` | Approved argv-array execution, timeout, bounded output, and exit code |
| `start_job`, `job_status`, `cancel_job` | Start approved bounded background commands, inspect logs and cancel |
| `request_restart` | Request approved process replacement after saving tool results |

File tools exclude `.env` credentials, common key formats, Git internals, virtual environments, generated directories, and `.codine`. They reject escaping paths and symlinks. Reading or editing hard-linked files is refused. Text files are limited to 2 MB; each read page carries at most 24,000 content characters. The next-page cursor handles even a single very long line. Pass the returned hash as `expected_sha256` on later pages to detect a changed file.

Edits require a fresh read and the exact changed region to have been returned. After an edit, re-read before editing another region. A second file check after approval protects against ordinary concurrent edits. These checks are not an OS sandbox or protection against a malicious process racing filesystem operations.

The default host executor runs with your user permissions. `shell=False` prevents automatic shell interpolation, but approved interpreters can still execute arbitrary code and access files/network outside the project. `--executor docker` instead runs an explicitly approved immutable snapshot without host mounts or network by default. User-owned execution profiles can permit narrowly scoped network/credential access with fresh approval; credential-bearing output is withheld. File tools remain host operations. `/sandbox` reports the effective mode. There is no delete tool yet, but approved edits and host commands can still break the application.

Foreground command timeout is 30 seconds by default, at most 120. Host results retain up to 12,000 bytes per stream and stop at 1 MB total; Docker has its own bounded output/resource policy. Background jobs default to 300 seconds and permit up to 3600, with four active jobs and bounded logs. Output streams live within these limits. The model loop defaults to 12 steps; provider network timeout is 120 seconds. Inference retries remain disabled when billing/replay safety is unknown. Automatic semantic compaction uses the same accounted provider path; it can incur an additional model request. See [compaction](docs/SEMANTIC_COMPACTION.md), [artifacts](docs/ARTIFACTS.md), [jobs](docs/BACKGROUND_JOBS.md), and [run states](docs/RUN_STATES.md) for limits.

## Sessions and restarting

One `Session` owns the conversation. Each snapshot is atomically replaced, saved with mode `0600`, and protected by an advisory lock. Full history stays on disk while a character-budget projection selects complete recent turns for the model. This is deterministic trimming, not a model-generated summary or exact token counting. Session loads are limited to 20 MB; start a new session before an archive becomes that large.

Before executing tools, Codine saves the assistant's requested batch in a journal. It saves each result, then commits the complete call/result group to history. After a crash, missing results become **outcome unknown** entries. Actions are never automatically replayed; inspect the project before retrying.

Changing source on disk does not replace functions already in memory. `/restart` validates owned Python files and runs a fresh `--check` subprocess before replacing the current process. It resumes the same project and session. Auto-edit resets to `ask`; explicitly selected planning mode is retained by `/restart`. A failed check keeps the old process alive. This catches syntax/import failures, not every logic bug.

For self-development, start with the Codine repository as `--project`. Read the relevant module, make one small change, run tests, then restart. Core paths (`main.py`, `codine/`, requirements, ignore rules, PRD, recovery guide), and `AGENTS.md`/`CODINE.md` at every project depth always require approval in auto-edit mode.

## Planning, instructions, and automation

```bash
python main.py --plan
python main.py --json --prompt /instructions
python main.py --json --check
python -m codine.evals --offline
python -m codine.evals --offline --executor docker --report .codine/evals/docker.json
```

Planning restricts both the advertised schemas and actual dispatch. It allows listing, reading, search, and syntax parsing; it does not allow commands, writes, or model-requested restarts. It does **not** disable model inference: typing a normal task still uses the configured model. Use offline slash commands/tests when no inference is intended.

Optional user guidance comes from `~/.config/codine/AGENTS.md`; project conventions come from root and selected ancestor-directory `AGENTS.md`/`CODINE.md`. Explicit user requests and harness rules outrank all documents; user-global guidance outranks project conventions, and deeper project guidance applies only to its subtree. Loading is bounded, policy-aware and non-executable; referenced files are not imported automatically. Newly discovered guidance defers model-requested mutations until the next request can see it. `/instructions [PATH]` and `/context` expose scope and provenance. See [instruction hierarchy](docs/INSTRUCTION_HIERARCHY.md) for limits and trust boundaries.

`--json` is a one-shot adapter with versioned JSONL records on stdout, including paired `tool_request`/`tool_response` IDs and a final `run_end` outcome. Approval is always denied in this mode. Step, output, and repeated-action limits exit with failure rather than claiming task success. `/restart` is interactive-only; scripts start another process using `--resume`.

## Optional OpenRouter configuration (for the later API stage)

Add the following to your existing `.env` only when ready to configure paid evaluations:

```dotenv
OPENROUTER_API_KEY=your-key
OPENROUTER_MODEL=your-verified-model-id
CODINE_BUDGET_USD=3.50
CODINE_REQUEST_BUDGET_USD=0.25
```

Select with `--provider openrouter`; NVIDIA remains the default. For a configuration check without inference, use `python main.py --provider openrouter --prompt /model`. An explicitly configured model is required. The OpenRouter adapter is offline-contract-tested; its live compatibility is not yet verified.

The ledger reserves $0.25 before each request and persists that intent. It settles against actual `usage.cost`; a connection error or missing cost keeps the reservation and blocks new requests. Inspect the provider's activity record, then use `/budget reconcile ACTUAL_USD` for that one request. Do not guess zero. If a charge exceeds the reservation/limit, the ledger blocks further requests.

This is a **per-session local guard, not a remote hard cap**. One request can cost more than its reservation, and other sessions/processes spend independently. Set an OpenRouter key credit limit before paid runs. The $1.50 reserve is a plan, not money isolated by this program. New sessions do not replenish your account.

## Learn the implementation

1. `main.py` calls `codine.cli.main()`.
2. `registry.py` converts registered tools into API schemas and dispatches validated JSON arguments.
3. `workspace.py` implements project file operations and stale-edit checks.
4. `provider.py` joins streamed fragments into one validated assistant message.
5. `agent.py` runs the model → tools → results loop and saves at every boundary.
6. `sessions.py` restores state and repairs interrupted batches.
7. `context.py` selects complete turns for each request without changing the archive.
8. `terminal.py` handles input/output; `restart.py` replaces the process after checks.

Read the annotated flow and Python concepts in [PRD.md](PRD.md) before extending the registry.

## Verify

```bash
python main.py --check
python -m unittest discover -s tests -v
```

The suite uses temporary projects, fake providers, real harmless subprocesses, and pseudo-terminals. It never calls the model API.

**Do not run live tests during the current offline phase.** For the later, explicitly selected NVIDIA integration stage (uses quota):

```bash
python tests/live_smoke.py
```

That test automatically approves the small task in a disposable temporary workspace. Normal CLI sessions keep approvals on.

This MVP has linear sessions, one active task, no plugin ecosystem, no subagents, and no hot reload. Commands can use the explicit Docker executor; file tools still operate on the host. It is a practical learning harness, not full Pi/Codex parity. Local pre-refactor copies are preserved under ignored `.codine/backups/pre-mvp/`; your sample `example.py` is kept separate from implementation commits.

The [Docker executor](docs/SANDBOX.md) uses disposable containers without network or host mounts. Launch `codine --executor docker --image python:3.12-slim` with an already installed image to isolate interactive commands. `/sandbox` shows the effective mode; Docker requests require explicit file selection and approval of the snapshot hashes. Sessions and restarts retain the pinned executor image. New sessions default to host mode, whose approvals explicitly describe full user permissions. Enable real Docker boundary tests with `CODINE_DOCKER_TESTS=1`; no model inference is involved.

## Preview the terminal design offline

```bash
.venv/bin/python scripts/preview_ui.py
```

This renders a fictional read/edit/review transcript without calling a model or changing project files. The design takes cues from [Pi's transcript/editor/footer separation](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/usage.md), [Claude Code's keyboard-driven interaction](https://code.claude.com/docs/en/interactive-mode), and the [Codex CLI reference](https://github.com/openai/codex). Codine retains its own key bindings and approval rules; this is a visual redesign, not feature parity.

## Model selection and file recovery

With OpenRouter selected, `/model` opens the current tool-capable model catalog and `/model free` filters advertised free routes. The selection stays in the session; it does not reset spending. Use arrow keys and Space to select, Tab to reach the buttons, Enter to confirm, or Escape to cancel.

`/changes` lists recent file-tool checkpoints. `/diff [ID]` compares a checkpoint's before-image with the current file. `/undo [ID]` previews restoring it and asks for approval; omit the ID for the newest checkpoint. Newer edits cause a refusal. This does not undo shell commands or external side effects. A successful undo informs the model in the conversation.

The `replace_lines` tool is an alternative to exact substring editing. It requires an inclusive line range already read and the current file's SHA256. It preserves the same approval, stale-file, and checkpoint protections.

See [PRACTICAL_RELEASE.md](docs/PRACTICAL_RELEASE.md) for a learning guide and an honest record of successful and failed live runs.
