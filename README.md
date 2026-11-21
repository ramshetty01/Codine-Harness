# Codine-Harness

A Pi-inspired Python terminal coding assistant, built to be understandable. Codine can inspect a project, propose file changes, run approved checks, save its conversation, and restart into updated harness code.

Read **[PRD.md](PRD.md)** for the detailed architecture, concepts, code walkthrough, and acceptance requirements. **[VERIFICATION.md](VERIFICATION.md)** records what was actually tested. **[RECOVERY.md](RECOVERY.md)** explains how to recover from a broken self-edit.

## Start

Python 3.11+ on macOS/Linux is the intended platform; verification used Python 3.14 on macOS. Windows is not supported by this version's POSIX locking and process-group code.

```bash
cd /Users/ramshetty/Desktop/ram-code/ramscode/codine
source .venv/bin/activate
python main.py
```

For a fresh checkout:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

Add your NVIDIA API key to `.env`. **Do not replace an existing `.env` when updating.**

```dotenv
NVIDIA_API_KEY=your-key
NVIDIA_MODEL=nvidia/nemotron-3.5-super-vl-120b-a12b
CODINE_MAX_TOKENS=4096
CODINE_MAX_STEPS=12
CODINE_CONTEXT_CHARS=160000
```

The OpenAI Python SDK sends requests to **NVIDIA**, at `https://integrate.api.nvidia.com/v1`. An OpenAI key is not needed. Existing shell variables override `.env`; existing output-budget settings also override the defaults. The app's `.env` loads first, then the selected project's `.env` supplies values that are still unset.

`python main.py --check` validates syntax and startup without an API request or session write. Offline slash commands work without a key. Model access, quotas, and compatible parameters depend on NVIDIA.

## Use it

The terminal opens as a compact workbench: a framed project/model/session card, a teal input prompt, and a status strip showing the project, edit policy, and token usage. It keeps your terminal's normal scrollback.

Type `/` to open the command menu. Use arrow keys to browse and Tab to complete a command. Tool activity uses plain labels (Read file, Edit file, Run command); results distinguish success, declined actions, and failures. Long previews are shortened for readability; `/details` shows the full last tool result. Added diff lines are green, removed lines red, and approvals appear in an amber review card. Narrow windows wrap cards rather than hiding paths. Set `NO_COLOR=1` for monochrome output.

- **Enter** submits; **Alt+Enter** adds a line. A terminal supporting bracketed paste inserts a multiline paste as one editable prompt.
- If your terminal does not preserve pasted lines, enter `/paste`, paste the task, then put `/end` on its own line.
- Diffs appear before writes. Type **yes** to approve; any other answer declines.
- Ctrl+C cancels the current input or model turn. Completed actions remain recorded. `exit`, `/quit`, or Ctrl+D at the editor saves and exits.
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
| `/session`, `/sessions` | Show current session details / list saved IDs |
| `/save` | Save immediately; saving is also automatic |
| `/resume ID`, `/new` | Open saved history / start a new session |
| `/clear` | Remove old turns from active model context, keeping the disk archive |
| `/compact` | Keep the most recent three complete turns in active context |
| `/restart` | Save, validate, launch the same interpreter, and resume |
| `/model [ID]` | Show or change the NVIDIA model for this session |
| `/thinking` | Toggle reasoning display |
| `/details` | Show the full output from the most recent tool |
| `/permissions ask` | Require approval for every file mutation |
| `/permissions auto-edit` | Approve ordinary file edits for this process; still ask for core edits and commands |
| `/paste` | Collect lines until `/end` |
| `exit`, `/quit` | Save and exit |

There is no live OpenRouter model popup in this NVIDIA runtime. `model_picker.py` and its standalone tests are retained as an earlier learning exercise. `/model ID` does not promise compatibility with every NVIDIA model: the adapter uses streaming tool calls and the Nemotron thinking parameters.

## Tools and limits

| Tool | Behavior |
|---|---|
| `list_files` | Recursive, filtered listing; narrow the directory when truncated |
| `read_file` | UTF-8 text, line numbers, SHA256, and a next-page cursor |
| `search` | Literal text search with file/line results |
| `write_file` | Create a new text file; never overwrite an existing one |
| `edit_file` | Replace one unique exact occurrence in a region already read |
| `check_python` | Parse syntax without executing |
| `run_command` | Approved argv-array execution, timeout, bounded output, and exit code |
| `request_restart` | Request approved process replacement after saving tool results |

File tools exclude `.env` credentials, common key formats, Git internals, virtual environments, generated directories, and `.codine`. They reject escaping paths and symlinks. Editing hard-linked files is refused. Text files are limited to 2 MB; each read page carries at most 24,000 content characters. The next-page cursor handles even a single very long line. Pass the returned hash as `expected_sha256` on later pages to detect a changed file.

Edits require a fresh read and the exact changed region to have been returned. After an edit, re-read before editing another region. A second file check after approval protects against ordinary concurrent edits. These checks are not an OS sandbox or protection against a malicious process racing filesystem operations.

Commands run with your user permissions. `shell=False` prevents automatic shell interpolation, but an explicitly approved interpreter or shell can still execute arbitrary code. Commands may access files and network outside the project. The child environment omits API credentials; that does not prevent a command from reading `.env` itself. Inspect each command before approving it. There is no delete tool, but approved edits and commands can still break the application.

Command timeout is 30 seconds by default, at most 120. Results retain up to 12,000 bytes from each output stream. More than 1 MB of total output stops the process group. The model loop defaults to 12 steps; provider network timeout is 120 seconds, with automatic API retries disabled.

## Sessions and restarting

One `Session` owns the conversation. Each snapshot is atomically replaced, saved with mode `0600`, and protected by an advisory lock. Full history stays on disk while a character-budget projection selects complete recent turns for the model. This is deterministic trimming, not a model-generated summary or exact token counting. Session loads are limited to 20 MB; start a new session before an archive becomes that large.

Before executing tools, Codine saves the assistant's requested batch in a journal. It saves each result, then commits the complete call/result group to history. After a crash, missing results become **outcome unknown** entries. Actions are never automatically replayed; inspect the project before retrying.

Changing source on disk does not replace functions already in memory. `/restart` validates owned Python files and runs a fresh `--check` subprocess before replacing the current process. It resumes the same project and session with permissions reset to `ask`. A failed check keeps the old process alive. This catches syntax/import failures, not every logic bug.

For self-development, start with the Codine repository as `--project`. Read the relevant module, make one small change, run tests, then restart. Core paths (`main.py`, `codine/`, requirements, ignore rules, PRD, recovery guide) always require approval.

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

Optional live integration test (uses NVIDIA quota):

```bash
python tests/live_smoke.py
```

That test automatically approves the small task in a disposable temporary workspace. Normal CLI sessions keep approvals on.

This MVP has linear sessions, one active task, no plugin ecosystem, no subagents, no sandbox, and no hot reload. It is a practical learning harness, not full Pi/Codex parity. Local pre-refactor copies are preserved under ignored `.codine/backups/pre-mvp/`; your sample `example.py` is kept separate from implementation commits.
