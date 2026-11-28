# Isolated editing trees: P3 foundation

The host API in `codine.isolated_worktrees` allocates independent, exact-source editing directories and integrates explicitly reviewed text changes through Codine's existing grouped checkpoints. **These are source snapshots, not linked Git worktrees or branches.** P3 remains open: linked Git semantics, lifecycle changes and supervised editing-worker runtime are not complete. P1/P2 read-only workers are unchanged; this module never creates a provider, key or spending allowance.

```python
from codine.isolated_worktrees import IsolatedTrees

manager = IsolatedTrees(parent_workspace)
first = manager.allocate(['src/parser.py', 'tests/test_parser.py'])
second = manager.allocate(['src/parser.py', 'tests/test_parser.py'])
child_workspace = manager.workspace(first['id'])
# Edit first['path'] through the child Workspace's normal guarded file tools.
review = manager.review(first['id'], new_paths=['tests/test_new_case.py'])
print(review['preview'])
# Explicit host/user action only; existing file approvals still apply.
result = manager.integrate(review['review'])
```

Only include a new path in `new_paths` after actually creating it. There is no recursive discovery of extra child files. New paths not explicitly selected remain isolated and unintegrated. Every originally selected file is checked, so deletion or rename of a base file fails explicitly instead of disappearing from review. No model-visible integration tool, automatic merge or CLI command is installed by this foundation.

## Allocation and policy

Allocation accepts 1–32 explicit supported source/documentation paths, at most 1 MB each and 8 MB total, using existing descriptor-based regular-file/single-link protections and staging path restrictions. Parent read policy applies before bytes are copied. Credentials, symlinks, hardlinks, Git internals, state and generated directories cannot be selected. UTF-8 text only; binary data is unsupported.

The default private root is `STATE_ROOT/editing-trees`. Each random-ID directory has a private `base.json` receipt and separate writable `tree` subtree. The receipt binds the canonical parent path/directory identity and original file hashes, modes and descriptor identities. Only the tree subtree is exposed by the child Workspace; parent model file tools protect the entire managed root. A restarted manager can reopen allocations, but reviews are deliberately process-local and must be repeated.

No Git command runs during allocation/review/integration. Git hooks, filters, templates, configuration and credential helpers therefore cannot execute or be inherited. This avoids unsafe checkout semantics while a true hardened linked-Git-worktree implementation remains outstanding. No parent changes, untracked files, `.env`, session archive or local user state are copied implicitly.

Child file policy maps relative paths back to the original parent project's rules. A child write still uses ordinary previews and approval rules. The child's edits and reads confer no parent read/edit permission. This is file-tool isolation, **not an OS sandbox** for arbitrary host commands or trusted executable extensions. Do not attach unrestricted execution to a tree and assume it cannot reach the parent.

## Review, conflict and recovery

Review returns a bounded combined diff and digest of the exact proposal. Integration requires that digest from the current manager process, rechecks child snapshots and the original parent states, and refuses conflicts before the first parent mutation. A newly selected child file cannot overwrite an existing parent file, including an untracked one. Disjoint changes from different trees can integrate; conflicting changes require human resolution and a fresh base allocation.

Integration uses a nonblocking project lock to serialize cooperating integrations. It constructs independent complete-file read coverage, shows the existing combined changeset preview, requests existing per-file approvals, and rechecks the child and parent after every approval. File-mode changes, deletes and renames are currently rejected; no broad `git reset`, checkout, auto-stash or textual conflict guessing occurs. Planning mode cannot integrate.

The existing grouped-checkpoint machinery reserves all originals before writes. If the last write fails after earlier writes succeed, use `/changeset` and `/rewind CHECKPOINT_ID` in the parent to inspect/recover; unrelated concurrent edits still block unsafe recovery. An integration is **not an atomic filesystem transaction**: other processes outside the cooperative lock can change files between final checks and replacement. The established per-file stale checks narrow that race but do not promise atomic compare-and-swap against arbitrary external writers.

A failed or cancelled allocation can leave a private, incomplete directory; it cannot be reopened without its valid receipt. Automatic pruning of trees is not implemented. Keep useful trees until their integration/recovery is resolved, then remove their known managed directory outside the model tool interface. No cleanup/reset operation is run against the parent.

Acceptance tests create two actual editing directories, integrate disjoint edits, reject conflicting parent/untracked changes, reject child changes during approval, preserve policy/credential boundaries, and inject a partial multi-file write failure followed by checkpoint recovery. All use local fixtures; no provider request is made.
