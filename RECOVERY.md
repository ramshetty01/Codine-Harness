# Recovering Codine

Use a separate terminal if Codine itself no longer starts. Do not ask a broken running agent to fix itself indefinitely.

## 1. Stop and inspect

```bash
cd /Users/ramshetty/Desktop/ram-code/ramscode/codine
git status --short
git log --oneline -10
git diff -- main.py codine requirements.txt
```

Choose a commit you know worked. `HEAD` means the latest commit, which is not automatically the last working version.

## 2. Preserve your current work

These commands create a local ignored backup; they do not reset anything:

```bash
mkdir -p .codine/backups
recovery_dir=$(mktemp -d .codine/backups/recovery.XXXXXX)
git diff --binary > "$recovery_dir/tracked-changes.patch"
cp -R codine "$recovery_dir/codine"
```

If `main.py` still exists, copy it too:

```bash
cp main.py "$recovery_dir/main.py"
```

The patch captures unstaged tracked changes only. If you have staged edits, save them separately with `git diff --cached --binary > "$recovery_dir/staged-changes.patch"`. Copy any additional untracked files you want to preserve. Do not publish backups or session files: they may contain private project data.

The original pre-MVP `main.py` and `example.py` were also preserved locally in `.codine/backups/pre-mvp/`. They represent the old script, not the new modular runtime.

## 3. Restore selected code from a known working commit

Set this to the chosen commit hash, then restore only the paths you intend to replace:

```bash
working_commit=REPLACE_WITH_KNOWN_WORKING_COMMIT
git restore --source="$working_commit" --worktree -- main.py codine requirements.txt
```

**This replaces local contents of those tracked paths.** It leaves `.env`, `.codine/sessions`, and unrelated files alone. If only `main.py` was deleted and its latest committed version works:

```bash
git restore --source=HEAD --worktree -- main.py
```

Restoring tracked paths does not remove newly created untracked modules. Inspect those separately if a broken import still appears. No `git reset --hard`, blanket checkout, or automatic stash-pop is required.

## 4. Verify before starting a model task

```bash
source .venv/bin/activate
python main.py --check
python -m unittest discover -s tests -v
python main.py --continue
```

If a dependency changed, reinstall the restored `requirements.txt` into the virtual environment. A syntax check alone does not verify runtime behavior.

## Session problems

- **“Already open in another Codine process”:** close the other process using that session. Lock files can remain on disk; deleting one is not a safe way to release an active advisory lock.
- **Malformed session:** keep the original JSON for inspection. Start a new session with `python main.py`, then use `/sessions` to find another valid session. Do not erase all history to fix one file.
- **Interrupted action:** read the affected files or inspect process/output state before retrying. “Outcome unknown” means the action may already have happened; it is not a promise that nothing changed.
- **Disk/save failure:** the app stops to avoid further unrecorded actions. Fix storage permissions or free space before resuming. The last durable journal remains the recovery point.
- **Session near 20 MB:** begin a new session and provide a brief task handoff; keep the old archive. The MVP does not rotate or summarize large archives automatically.
- **Restart check failed:** the old process continues running. Repair the source externally or through an approved edit, then retry `/restart`.

## Before future self-modification

Run the checks, review the exact diff, and commit only intended files:

```bash
git diff --check
git diff -- main.py codine tests README.md
git add main.py codine tests README.md
git commit -m "Describe the verified change"
```

Inspect `git status` before staging. Avoid `git add -A` when unrelated work or credentials might be present. The absence of a delete tool is not a sandbox: an approved command or bad code edit can still damage the project.

## Recover one file-tool change

In an interactive Codine session, use `/changes`, then `/diff ID`, then `/undo ID`. Omit the ID for the latest checkpoint. Review the diff and approve only the intended restoration. The file must still match the recorded after-image; otherwise Codine refuses the undo. `/undo` does not restore shell side effects, and it does not replace Git recovery. It can recover file-tool changes across restarts while `.codine/changes/` remains intact. A checkpoint written immediately before a failed/crashed write may exist without an applied change; the hash guard will refuse a mismatched state.

## Private diagnostics and session compatibility

Create a local diagnostic without copying conversation or file contents:

```bash
python -m codine.diagnostics --project . --session SESSION_ID --category persistence
```

Omit `--session` for runtime metadata only. Categories are authentication,
rate_limit, connection, persistence, invalid_session, unsupported_version and
unknown. The bundle contains fixed recovery guidance, runtime versions and session
counts/boolean journal status. It excludes keys, environment, project paths,
model names, prompts, tool arguments, raw exceptions and source contents. Files
are created with mode 0600 under `.codine/diagnostics`; no upload occurs. Review
metadata before sharing. Malformed or future-version sessions are classified
without modifying them, and output symlinks are refused.

Sessions currently use schema version 1 with optional fields for additive
features. Keep a separate private backup of `.codine` before upgrading or
attempting recovery. Missing optional fields use their documented defaults;
unknown future versions are rejected rather than guessed at. There is no general
cross-version migration command yet: use the matching Codine release to read a
newer session or start a new session while preserving the original. Never change
the version number manually to bypass validation.

For uncertain tool outcomes, inspect files before reissuing the action. For an
uncertain provider charge, reconcile the provider's actual bill before continuing;
restarting or restoring a conversation must not be used to erase a pending charge.
For full disk/write failures, free space or correct permissions before saving,
and retain the last intact session. Diagnostics do not repair corrupt snapshots
or replay tools. Git restores code, not conversation state or billing records.
