# Local extension packages

`/packages` installs reviewed local text/source packages into
`.codine-extensions/NAME` in the selected project. It does not download packages,
open archives, run installers, run `pip`, import extension code, load workers, or
grant trust. This first format deliberately supports local project-relative
directories only. Installation and removal are idle terminal actions, require
approval, and are unavailable in plan mode. DENY file policies remain enforced.

A source directory contains `package.json`, the existing extension manifest, and
its explicitly declared files. For example:

```json
{
  "schema_version": 1,
  "name": "demo",
  "version": "1.2.0",
  "extension": "manifest.json",
  "python": {"min": "3.11", "max_exclusive": "4.0"},
  "api": 1,
  "dependencies": {}
}
```

The existing extension manifest remains unchanged:

```json
{
  "version": 1,
  "name": "demo",
  "entrypoint": "main.py",
  "files": ["main.py", "docs/guide.md"],
  "capabilities": ["commands"]
}
```

The package and extension names must match. Package names contain lowercase
letters, digits, underscores and hyphens, starting with a letter, at most 48
characters. Package versions have one to four numeric components separated by
dots. This is not a PEP 440 dependency resolver. `python.min` is inclusive and
`max_exclusive` is exclusive, both `major.minor`. `api` must match the installed
Codine extension API version. Dependency entries map distribution names to exact
installed version strings, for example `"requests": "2.32.3"`. Checks read
installed distribution metadata through `importlib.metadata`; they do not import
the dependency or install/update it. Missing versions and mismatches block
installation. The same interpreter/site-packages environment is used by extension
workers. Dependencies of dependencies are not resolved or attested.

## Review and install

```text
/packages review extension-source
/packages install EXACT_REVIEW_DIGEST
/packages list
/extensions discover .codine-extensions
```

Review returns compatibility results, destination, file hashes and an exact
snapshot digest. Only declared source files and the two manifests are copied;
unlisted files are not silently included. Before installation, Codine prints the
file additions as a diff and asks for approval. Source bytes/identities and
compatibility are checked again after approval. It refuses existing destinations,
loaded packages, source changes and incompatible dependencies. Copying uses the
reviewed snapshot, not an installer. Directory publication uses atomic
no-replace rename on Linux/macOS, so even a concurrently created empty destination
is not overwritten. Unsupported libc/filesystem/platform behavior fails closed;
there is no fallback to an overwriting rename.

Installation grants no execution authority. The installed paths and file
identities differ from the source, so review and grant trust separately:

```text
/trust review .codine-extensions/demo/manifest.json
/trust allow EXACT_INSTALLED_SNAPSHOT_DIGEST
/extensions load .codine-extensions/demo/manifest.json
```

## Removal and recovery

```text
/extensions unload demo
/packages remove demo
```

Removal checks the installation receipt, directory/file identities, hashes, full
contents, loaded state and file write policy, then asks for approval and checks
again. Modified files, replaced files even with identical bytes, extra files or
directories, links and loaded extensions block normal removal. A successful
removal atomically moves the directory out of discovery into a protected backup
under `.codine/packages/removed-UUID`. It does **not** permanently delete bytes or
revoke a trust receipt. `/packages list` shows its status and backup location.
Moving a package back does not by itself grant a new trust approval.

Receipts, staging and backups live in excluded/protected project state, with
private files, checked owned directories and a nonblocking cross-process lock.
Pending receipts precede filesystem publication/moves. A crash or receipt-write
failure can leave `pending_install`, `pending_remove`, or
`removal_needs_review`. These states block normal reuse/removal and retain bytes;
they are not reported as successful operations. A racing change detected after
removal is retained in the backup for inspection. No rollback overwrites a newly
created destination.

There is intentionally no automatic repair, purge, upgrade or same-name reinstall
in this first version. For pending/removed entries, use `/packages list`, stop
Codine processes touching the project, and inspect the receipt and the reported
staging/destination/backup outside the harness. Preserve all copies before any
manual repair. Do not delete a pending receipt merely to retry: the filesystem
operation may already have succeeded. Receipts bind inode identities and should
not be edited to pretend modified files are an unchanged installation. Retention
and recovery automation are future work; normal removal is reversible
**deactivation of the installed location**, not a general transactional package
manager or OS sandbox.

## Limits and checks

At most 32 declared extension files plus two manifests, 1 MiB total reviewed
bytes, 16 KiB package metadata, 64 KiB extension metadata, eight path components,
64 subdirectories, 16 in-memory reviews, and 64 stored package receipts are
supported. Paths must be normalized relative paths; supported UTF-8 source,
configuration and documentation types only. Credentials, generated/internal
paths, symlinks, hardlinks and special files are refused. Package directories must
be owned by the current user and not group/other writable. These checks do not
protect against a malicious process already running as the same OS user.

```bash
python -m unittest tests.test_extension_packages tests.test_rename_noreplace -v
```

Tests include changed source/destination, compatibility changes, policy denial,
interrupted publication, retained racing changes, real installed-package
trust/load/unload, and no-clobber directory publication. The rename boundary was
also exercised inside a local Linux Python container with networking disabled,
no host mounts, a read-only root and temporary scratch space. Native flags follow
[Linux's UAPI definitions](https://github.com/torvalds/linux/blob/master/include/uapi/linux/fs.h)
and the installed macOS SDK's `sys/stdio.h` (`renameatx_np`, `RENAME_EXCL`).
No model API calls are needed for these checks.
