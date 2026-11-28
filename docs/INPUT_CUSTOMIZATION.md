# Prompt history, search and external editing

Interactive prompts now keep private history across runs. **Ctrl+R** starts
reverse search; type a substring, press Enter to accept the matching draft, then
Enter again to send. Up/Down also browse history. Search and recalled text remain
editable. This history contains submitted prompt text, not attachment contents,
model output or approval answers. Approval prompts use a separate input session.

History lives at `~/.local/state/codine/input-history.json`, with a private lock
alongside it. Bounds are 500 recent entries, 256 KB total, and 16,000 characters
per entry. Longer prompts still work but are not added to this history. Oldest
entries are discarded to meet the limits. Known keys and common credential
patterns are redacted before entering searchable history and before persistence;
unknown or encoded secrets may still remain. Treat the file as private. Remove
it while Codine is closed to clear history.

Files use mode 0600, descriptor-based no-symlink/no-hardlink reads, private atomic
replacement, and a lock to merge concurrent writers. A corrupt, unsafe or busy
history is preserved, not overwritten. A visible warning explains when new input
could not be saved; the current prompt remains usable. In-memory history is also
bounded. The PTY tests use isolated temporary homes, not your real history.

Create `~/.config/codine/input.json` yourself, outside the project, with mode 0600:

```json
{
  "version": 1,
  "history": true,
  "keys": {
    "submit": ["enter"],
    "newline": ["escape", "enter"],
    "history_search": ["c-r"],
    "editor": ["c-x", "c-e"]
  },
  "editor_argv": ["/usr/bin/vi"]
}
```

Settings take effect at startup. Omit keys to keep defaults. Set `history` to
false to disable both saved and in-memory prompt history. Bindings use
prompt-toolkit key names and one or two sequential keys; for example `f6` is a
single function key, while `escape`, `enter` is Alt+Enter. Duplicate/prefix
conflicts, invalid keys, and reserved interrupt/EOF/tab/paste bindings fail
validation. Configured bindings apply to the main/busy prompt, never approval
answers. Startup displays the effective bindings. Enter during search accepts
the search result rather than submitting a model request.

**Ctrl+X Ctrl+E** opens the current unsent draft in the configured editor.
`/editor` opens a blank draft or a draft preserved while busy. When busy, editor
requests are queued for the main prompt; no second terminal reader starts.
Editor text returns as an **unsent draft**. Review it, then submit normally.
Cancellation, failure, invalid UTF-8, unsafe replacement files or oversized
output preserve the original draft. No model request happens merely by editing.

`editor_argv` is a literal argument list with an absolute executable outside the
project; Codine appends the temporary text-file path. `$EDITOR`, `$VISUAL`, project
settings and model text never choose a command. No shell interpolation occurs.
Use a terminal editor or a GUI editor's blocking/wait option. Editors are trusted
user programs, not sandboxed tools; your own editor configuration/plugins still
apply. Existing script/config path arguments inside the project are refused.
The editor runs from a private temporary directory with a restricted environment
captured before project dotenv loading, not from the repository.

Drafts are limited to 64 KB. The temporary file is private and removed on success
or failure. The editor gets foreground terminal ownership where available;
Codine restores it afterward, limits execution to ten minutes, and cleans up the
editor process group. This POSIX implementation supports macOS/Linux terminals
and compatible IDE PTYs. It does not promise recovery after a process crash or
machine power loss. Input settings/history paths are excluded from model file
tools; project-local settings cannot authorize editor execution.

Offline checks:

```sh
python -m unittest tests.test_input_settings tests.test_terminal_integration
```
