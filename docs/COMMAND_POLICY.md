# User execution and file policy (E6)

Commands ask for approval by default. Optional user rules can allow, ask, or deny
specific command requests. Command rules apply to command execution. Optional file rules, described below,
control harness reads and writes while keeping built-in exclusions, core-file
approval, planning restrictions, and restart approval intact.

Codine reads `$CODINE_HOME/command-policy.json` if the shell explicitly sets
`CODINE_HOME`; otherwise it reads `$XDG_CONFIG_HOME/codine/command-policy.json`
(default `~/.config/codine/command-policy.json`). Checkout/project configuration is
never an automatic source of command rights. `HOME`, `CODINE_HOME`, and
`XDG_CONFIG_HOME` cannot be set by project `.env`. Alternatively select a file
explicitly with `codine --policy /absolute/path/my-policy.json`.

The file must be owned by the current user, not writable by group/others, not a
symlink/hardlink/special file, and at most 64 KB. Missing default config means
**ask**. Missing explicitly selected config, malformed JSON, or invalid rules
abort startup; they never grant permissions. Rules are immutable for the process:
edit them outside Codine and restart to load changes. `/policy` shows the source,
all loaded rules, and the latest explained decision. The model's file tools cannot
read or edit the selected policy path, including in auto-edit mode.

Example (replace `/absolute/path/project` with the actual canonical project path):

```json
{
  "version": 1,
  "rules": [
    {
      "id": "unit-tests-in-docker",
      "action": "allow",
      "executor": "docker",
      "project": "/absolute/path/project",
      "cwd": ".",
      "match": "exact",
      "argv": ["python", "-m", "unittest", "discover", "-s", "tests", "-v"]
    }
  ]
}
```

All seven rule fields are required. `executor` is exactly `host` or `docker`;
project is an exact absolute canonical directory; cwd is an exact normalized
relative directory, not a glob or subtree. Argument matching compares array
entries without shell parsing. `exact` requires all arguments to match.
`prefix` explicitly permits **any additional arguments** after the declared array.
For example, a prefix `['python']` authorizes arbitrary Python commands and is very
broad. Prefixes are never inferred and wildcards have no special meaning.

Matching precedence is **deny > ask > allow**, irrespective of file order. No match
means ask. Changing executor, project, cwd, or exact arguments loses the match.
A Docker allowance cannot authorize a host request. Decisions include matching
rule IDs and are displayed before execution. Allowed Docker runs still display
the approved command and immutable manifest hashes; denied requests never run.
Host matching uses the requested `python`/`python3` spelling before Codine replaces
it with its own interpreter path, which is shown in the execution description.

An allow rule applies only in an interactive terminal. JSON and other
noninteractive modes downgrade allow to ask, whose approval is denied; rules do
not create a hidden unattended-execution switch. Planning mode still blocks all
commands. Rules cannot relax the separate core-edit approval path.

Command rules are **not code integrity guarantees**. A project can modify the
script run by an exact argv, and explicit host allow rules grant that command full
host user privileges, including filesystem effects. Use Docker scope when those
privileges are not intended. File-edit approval does not constrain the internal
behavior of a separately authorized host command. This version does not bind
rules to source hashes, image IDs, or expected network behavior.

Explicit CLI policy paths are carried through `/restart` as runtime state. They
are not trusted from saved session JSON: on a fresh manual `--resume`, select
`--policy` again if needed. Otherwise the user default policy applies. Sessions
continue to retain their separate executor/image settings.

## File rules

Version 1 now also accepts an optional `file_rules` array. Existing command-only
files remain valid. The combined command/file rule count remains at most 128.
File rules use `id`, `action`, `operation`, `project`, `match`, and `path`:

```json
{
  "version": 1,
  "rules": [],
  "file_rules": [
    {
      "id": "private-notes",
      "action": "deny",
      "operation": "read",
      "project": "/absolute/path/project",
      "match": "glob",
      "path": "private/**"
    },
    {
      "id": "generated-notes",
      "action": "allow",
      "operation": "write",
      "project": "/absolute/path/project",
      "match": "exact",
      "path": "notes/summary.md"
    }
  ]
}
```

`operation` is `read` or `write`. Project scope is an exact canonical absolute
path. `exact` selects one project-relative path. Explicit `glob` patterns use
segment-aware matching: `*`, `?`, and character classes stay within a path segment;
`**` matches zero or more directory segments. Thus `src/*.py` excludes deeper
files, while `src/**/*.py` includes them. Names are case-sensitive, with Unicode
NFC normalization. Existing paths must use their actual directory-entry spelling;
case aliases cannot bypass an exact rule on a case-insensitive filesystem.

Deny takes precedence over ask and allow. Unmatched reads retain existing allowed
behavior. Unmatched writes keep the selected ask/auto-edit mode. An explicit write
ask always prompts, even in auto-edit mode. An explicit ordinary-file allow skips
the write prompt only in interactive mode; it still shows the diff and preserves
read-before-edit, covered-region, size, hardlink and stale-content guards. Core
files always require approval. Plan mode blocks writes regardless of any rule.
Credential/internal-path exclusions, symlink restrictions and protection of the
policy file cannot be relaxed by allow rules.

Read-denied filenames are filtered from listing/search results, and actual reads
are rejected before file content is opened. Ask-protected filenames can be listed,
but reading their contents requires approval. Search can therefore prompt when
it reaches an ask-protected file. Write denial is checked before reading edit
contents or modifying files; undo uses the same write policy and stale guards.
Docker staging honors read rules before creating its immutable snapshot, even if
a command rule would otherwise allow execution automatically. Foreground and
background commands use this same staging boundary.

`/policy` displays both rule types and the latest decision. These are harness file
operation rules, not an operating-system sandbox: separately authorized host
commands can affect host files independently of file tools. Prefer Docker when
command containment is required.

Automatic root guidance loading also honors read rules: denied and ask-protected
AGENTS.md/CODINE.md are omitted without prompting on every model turn. The explicit
`/instructions` command can request approval for ask-protected guidance; denied
content is never loaded. Already-saved conversation history is not retroactively
rewritten when a user adds a read-deny rule.
