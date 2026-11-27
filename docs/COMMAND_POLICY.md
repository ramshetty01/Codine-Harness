# User command policy (E6, command layer)

Commands ask for approval by default. Optional user rules can allow, ask, or deny
specific command requests. This milestone covers command execution only: ordinary
file permissions, core-file approval, plan mode and restart approval keep their
existing behavior. It is not a complete filesystem permission rules engine.

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
commands. Rules do not affect the separate core-edit approval path.

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
