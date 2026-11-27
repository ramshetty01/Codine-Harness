# Git inspection and session baselines

Use `/git` for branch, HEAD, and scoped dirty paths. Use `/git diff PATH` for working file versus index, or `/git staged PATH` for index versus HEAD. The model has equivalent `git_status` and `git_diff` read tools. These commands never stage, commit, discard, or restore changes.

A new CLI session records a baseline before its first turn. Resumed sessions retain that baseline; older sessions without one record a first-inspection baseline, explicitly identified as such. Each dirty path can report whether it was dirty at baseline, whether its available fingerprint changed, and whether its bytes match a checkpoint from this session's file tools. That last field is evidence of matching output, not proof of exclusive authorship. Shell edits, concurrent editors, and old checkpoints may remain unattributed.

Status is limited to 200 permitted paths and fingerprints the first 32. Checkpoint inspection uses at most 64 records and 16 MB. Omitted fingerprints are unknown. Baselines contain metadata and hashes, never file contents. Git inspection does not grant the read ranges needed for editing.

File access exclusions and configured permissions apply to inspection. Automatic fingerprints skip files requiring approval. Explicit diffs request permission where required. Current contents use the workspace descriptor reader; diff runs on private snapshots. Global Git configuration, hooks, fsmonitor, external diff and text conversion are disabled. Index conflicts are reported rather than treated as an empty file. Blob reads have a 2 MB limit, diff output 60 KB, and each Git process a five-second deadline.

A missing Git installation does not prevent CLI startup. Non-repository projects remain usable. Syntax and offline tests do not measure model quality.

References: [Git porcelain status](https://git-scm.com/docs/git-status), [Git diff](https://git-scm.com/docs/git-diff).
