# Adversarial boundary regression tests (Q7)

`tests/test_adversarial_boundaries.py` exercises deterministic harness boundaries
with malicious inputs. No live model or API key is involved. This is **not** a
claim that a model is immune to prompt injection or that file tools constitute an
operating-system sandbox.

Coverage includes:

- Excluded credential paths, traversal, absolute paths, NUL paths, unexpected
  tool arguments, wrong argument types and booleans masquerading as integers.
- A harmless-looking file hardlinked to `.env`, both direct read and search.
- A file replaced with a credential symlink between resolution and actual read.
- Hardlinked project guidance and guidance swapped to a symlink just before open.
- A hardlink introduced during staging, detected by the final descriptor checks.
- Malicious project guidance that demands a write despite planning mode; real
  registry dispatch still refuses the requested mutation and records valid pairs.
- C0/C1 control characters in tool stdout, full output details and JSON events.

The tests first reproduced five failures. The fixes reuse the staging reader's
open-directory descriptors, `O_NOFOLLOW`, regular-file/single-link checks, bounded
reads and before/after identity checks for workspace reads and project guidance.
Reads that race or encounter links fail rather than returning the fixture's secret
canary. The terminal renderer strips C1 controls as well as C0 controls. JSON output
ASCII-escapes Unicode, keeping structured data losslessly decodable while preventing
raw C1 bytes/codepoints from reaching a terminal displaying JSONL output.

Limits: normal project instructions can still influence a model's decisions within
its permitted capabilities. These tests verify dispatch policy, not model behavior.
They do not prove every filesystem race, write boundary, external command or terminal
emulator safe. Docker command isolation, policy approval, source selection and
secret redaction remain separate mechanisms with separate tests. Hardlinked files
are now intentionally refused by read/search and guidance loading, even when a
particular hardlink is benign.
