# Instruction skills

Skills are optional text packages that teach Codine a workflow. They do not install
Python modules, register executable tools, grant file access, authorize a command,
change budgets, or bypass extension trust. Script references are read as text;
execution still requires the ordinary command tool and its approval policy.

Put a package at `.codine-skills/demo/SKILL.md` in the selected project, or at
`~/.config/codine/skills/demo/SKILL.md` for user-owned skills. The user directory is
fixed independently of project settings, protected from model file tools, and
must have user-owned files and directories not writable by other users. User
skills are disabled if their directory falls inside the selected project (for
example, selecting HOME); there is no fallback that broadens project access.

A minimal file:

```markdown
---
name: demo
description: >-
  Explain a small Python module and suggest
  a focused test before changing it.
---
Read the relevant module. Explain its inputs, outputs, and side effects.
See references/testing.md for the suggested test workflow.
```

The directory and `name` must match: lowercase letters, digits, underscores and
hyphens, starting with a letter, at most 64 characters. This implementation
supports a deliberately small YAML frontmatter subset, **not general YAML**:
only unique `name` and `description` keys; plain scalars, single quoted scalars
(with doubled apostrophes), JSON-compatible double quoted strings, and `>`, `>-`,
`|`, `|-` blocks indented by two spaces. Folded blocks join lines with spaces;
literal blocks join them with newlines. Comments on separate lines are allowed.
Inline comments, aliases, anchors, tags, collections and additional fields are
rejected. Unsupported packages are reported as unavailable, never evaluated.

Use `/skills` to discover metadata, and `/skill project:demo` to display and
activate a skill's full text for subsequent requests. `/skill user:demo` selects
the user package. Bare names work only when exactly one discoverable package
matches; qualify names when ambiguous or when explicit read approval is needed.
`/clear`, `/new`, `/resume`, transcript import, undo/rewind, and process restart
clear active skill receipts. Explicitly activated text is a snapshot until
reloaded or cleared; existing historical tool results remain in the archive.
Neither archive contents nor imported text recreate reference access receipts.

For the model, each turn includes a bounded catalog of names, descriptions and
header hashes, labelled untrusted instruction data. It does **not** include all
skill bodies. `discover_skills` refreshes that catalog. `load_skill` reads a full
package and returns its SHA-256; the result enters the normal tool history.
`read_skill_reference(name, path, expected_sha256)` requires a skill loaded in the
current active session. It rereads SKILL.md and refuses if its hash changed, so
old instructions cannot silently authorize references from a changed package.
The reference result has its own hash. Paths are normalized relative paths
inside that package; URLs, absolute paths, traversal, credential/generated paths,
symlinks and hardlinks are refused. No references are fetched automatically.

Project reads follow existing file policy. Automatic discovery never prompts
and omits metadata requiring approval; an explicit qualified load requests read
approval where needed. Reading a skill does not grant generic file edit rights.
Global text reads use the dedicated checked reader, not a broad HOME workspace.

Bounds: 256 directory entries scanned per scope, at most 32 catalog records and
12 KB of catalog JSON; frontmatter must close in the first 4 KB, and descriptions
are at most 600 UTF-8 bytes. Discovery reads only a prefix and validates the
file's stated size. Full SKILL.md files are limited to 32 KB, references to 16 KB,
and loaded skills to four per active session. References are supported source,
configuration or documentation UTF-8 files, at most 12 path components. Reads
check opened descriptors for regular-file type, single links and stable file
identity/size/timestamps before and after reading. This is instruction loading,
not an OS sandbox or full YAML parser.

Offline verification uses fake providers, temporary packages and file policies:

```bash
python -m unittest tests.test_skills -v
python -m codine --check
python -m codine.evals --offline
```

The scripted exercises verify harness behavior; they do not measure live model
skill selection or coding quality.
