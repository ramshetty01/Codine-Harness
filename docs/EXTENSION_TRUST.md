# Reviewing executable repository extensions

Extensions are opt-in executable Python code. **They run with your host user
privileges, not in an OS sandbox.** Declared capabilities describe Codine tools,
commands, events and UI surfaces. They cannot restrict filesystem access,
network calls, subprocesses or other Python behavior. Review source before trust;
never trust a package merely because its manifest requests few capabilities.

A version 1 manifest contains exactly these fields:

```json
{
  "version": 1,
  "name": "example",
  "entrypoint": "main.py",
  "files": ["main.py", "helpers.py", "data/example.bin"],
  "capabilities": ["tools", "commands", "events", "ui"]
}
```

Files are relative to the manifest's directory. The Python entrypoint must be
listed. All source and data files must be explicitly listed; no imports or code
execute during metadata inspection. A snapshot permits at most 32 files and
1 MiB of manifest plus file bytes, with a 64 KiB manifest limit. Binary data is
supported. Traversal, symlinks, hard links, special files, credentials and managed
state are rejected. Workspace read policy also applies, including read approvals.

Use the terminal commands:

1. `/trust review extensions/example/extension.json` displays the package name,
   entrypoint, declared capabilities, each file hash and the exact snapshot digest.
   Inspect the declared source separately; metadata review is not a security audit.
2. `/trust allow DIGEST` requires that review in the current process/project,
   rereads the package, asks for explicit terminal approval, then rereads again.
   A changed snapshot requires another review. Workspace automatic-write settings
   never bypass this approval. Noninteractive terminal approval declines.
3. `/trust list` displays persisted grants.
4. `/trust revoke DIGEST` removes a grant for future loads/actions. It cannot undo
   side effects already performed or stop arbitrary already-running Python code.

Grants bind the complete manifest bytes, declared source/data bytes, file and
manifest identities, project root path/identity and declared capabilities. A
copied/replaced file can require a new review even when its text is identical.
A loader must build a fresh snapshot, check `trusted(snapshot)` and stage exactly
those returned bytes, never reread a path after the check. Recheck the receipt
before future actions so revocation remains authoritative. This trust module
itself never starts workers or imports extension code.

Receipts live at `~/.config/codine/extension-trust/receipts.json`, independent of
project dotenv and checkout settings. State and its lock must be user-owned
regular files with exactly `0600` permissions. Parent directories are traversed
without following links and reject shared writes, except root-owned sticky
ancestor directories. Updates use a nonblocking lock and atomic replacement. A busy store fails closed
with a retry message instead of waiting indefinitely. Custom store roots used by
embedders are protected in the workspace at review and allowance time too. Model file tools
protect this entire root, including when the project itself is the user's home.
The protection applies to Codine file tools; it is not a restriction on trusted
host commands or extension Python code running with the user's privileges.

Offline validation covers exact grants, decline, changes before/during approval,
revocation, project/file identity, malicious paths, secret paths, links, byte/file
bounds, policy reads, invalid receipt permissions, broad-home filetool protection,
CLI approval and persistence failures. No provider requests are needed.
