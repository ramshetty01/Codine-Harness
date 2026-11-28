# Repository command task pack (Q1/R1 foundation)

Run `python -m codine.command_evals --offline`. Docker and a locally installed
`python:3.12-slim` image are required; the executor resolves that tag to its image
ID and never pulls an image or falls back to host execution. This runner sends no
model requests, reads no API key and does not spend evaluation credits.

The pack contains five synthetic mutations of real repository source, covering:

| Task | Category | Repair and independent validation |
| --- | --- | --- |
| boltons-clamp-upper | Bug fix | Restore upper-bound enforcement; validate lower bounds and invalid ranges too |
| boltons-bits-bytes | Feature restoration | Restore bytes conversion, including empty and leading-zero byte strings |
| boltons-clamp-module | Refactor | Move clamp to a separate module, preserve its public API, verify implementation ownership |
| boltons-floor-test | Test repair | Correct an inclusive-boundary expectation; final checker injects a wrong result to prove the assertion remains meaningful |
| boltons-interrupted-edit | Recovery | Repair a deliberately interrupted merge-conflict edit and restore behavior |

These are **not mined historical issues**. Source and license come from
[Boltons mathutils](https://github.com/mahmoud/boltons/blob/d31d821d56c8f7756af9c30a864077f2e5855321/boltons/mathutils.py)
at commit `d31d821d56c8f7756af9c30a864077f2e5855321` under its
[BSD-3-Clause license](https://github.com/mahmoud/boltons/blob/d31d821d56c8f7756af9c30a864077f2e5855321/LICENSE).
The full module, original copyright header and complete license are retained.
The license is staged as `LICENSE.txt`; the source hash is validated before use.
Fixture mutation descriptions, public tests and final checks are Codine-authored.

`evals/command_tasks.json` embeds shared source/license files, upstream provenance,
per-task initial overrides, reference replacement files, a public command and a
separate held-back final command. No download or dependency installation occurs
at runtime. Packaging includes the JSON fixture using the existing fixture bundle.

Each task uses a temporary project and four fresh restricted containers:

1. Run public checks on the broken fixture.
2. Run held-back final checks on the broken fixture.
3. Apply explicit reference replacement files, then run public checks.
4. Run held-back final checks on the repaired candidate.

The baseline must fail at least one required check; the repair must pass both.
A timeout, output-limit stop or nonzero exit cannot count as a pass. Refactoring
may pass baseline public behavior but fail the held-back structural requirement.
Only candidate files and public tests are staged. The task pack, reference repair
mapping and hidden check source are not copied into the project. The host supplies
the final checker as an explicit command only for its final container run. There
is no agent/model integration in this runner yet, so it does not claim live
`run_checks` isolation or model solve rates.

Every attempt gets a new UUID report under `.codine/evals/command-attempts/`, saved
before execution and after each task. Reports record command argv, actual exit
status, bounded stdout/stderr, manifest digests and immutable image IDs. Provenance
hashes all shared source, license, task mutations, reference repairs and checker
commands. Local reports contain held-back diagnostics; do not feed them to a model
and then call the same cases an unseen evaluation.

## Second repository: python-dotenv

Run `python -m codine.command_evals --offline --pack dotenv` (the default
`--pack boltons` retains the original corpus). Both selectors use the same
restricted Docker executor and per-attempt reports; reports also name the pack.

`evals/dotenv_tasks.json` adds five tasks using the complete dependency-free
[python-dotenv parser](https://github.com/theskumar/python-dotenv/blob/a565c2cc41599c48eabc6b7b7f5b826d43c5a6d7/src/dotenv/parser.py),
from release v1.2.4, exact commit
`a565c2cc41599c48eabc6b7b7f5b826d43c5a6d7`. The full
[BSD-3-Clause license](https://github.com/theskumar/python-dotenv/blob/a565c2cc41599c48eabc6b7b7f5b826d43c5a6d7/LICENSE)
is retained as `LICENSE.txt`. Both bundled bytestrings are verified before use:

- Parser SHA-256: `0bc1350444eeff987f2c22924400ad0b7727563798118c641cd4c0de55bd30d0`
- License SHA-256: `80619b7049f08c81683ad0e01f08f257a840652dd71ee83146d36658c7d2c2b9`

| Task | Category | Independent final evidence |
| --- | --- | --- |
| dotenv-line-provenance | Bug fix | LF, CRLF and bare-CR line numbers; exact original text; malformed-line recovery |
| dotenv-strict-mapping | Feature | New strict/lenient mapping API; duplicate last-wins; bare versus empty values; original error line; no interpolation |
| dotenv-extract-escapes | Refactor | Decoder moves to its own module; backward-compatible re-export and module ownership; quoting/Unicode/escape behavior |
| dotenv-bare-key-test | Test repair | Production parser hash unchanged; both bare and explicit-empty assertions pass; an injected wrong parser result must fail the repaired test |
| dotenv-interrupted-extraction | Recovery | Complete a half-applied two-module extraction; restore import contract and parsing behavior without losing unrelated support |

These are Codine-authored source-derived exercises, not historical upstream
issues. The feature adds a specified API absent from the pinned source; the other
tasks deliberately mutate or reorganize the pinned source/tests. Public tests are
real unittest commands. Final checks are independent commands supplied only to
the final container, including explicit expected outputs and structural checks.
The test-repair oracle introduces a wrong bare-key result to reject assertion
removal or weakened checks. Recovery means repairing a partially applied source
change here; durable agent-session interruption/replay behavior has its separate
[long-task gate](MILESTONE3_ACCEPTANCE.md).

Validation: both packs have five intended baseline/reference pairs. The new
python-dotenv pack passed 20 isolated command executions: every baseline failed
the final oracle, and each reference passed public and final checks. Zero model
requests were made. `tests.test_dotenv_corpus` verifies provenance, license/source
tampering, candidate/final-source separation and vacuous/timeout failure handling;
its Docker acceptance method requires `CODINE_DOCKER_TESTS=1`.

## Remaining scope

This is now ten tasks across two real upstream Python projects and all five
categories, still a small synthetic source-derived corpus. It does not represent
large application repositories, mined historical issues, multi-language builds,
or live model solve rates. The command runner validates references; it still has
no agent/model integration. The two older `repository_tasks.json` exercises are
synthetic package-import tests and are not counted as upstream repository tasks.

The [escape-string-regexp repository](https://github.com/sindresorhus/escape-string-regexp)
was previously inspected (HEAD `a7f7dabaf336bda98d2166848af15d9168b10bcf`). No Node
image is installed for its verification; none is silently pulled. Additional
languages require explicitly prepared images and independently verified oracles.
Do not feed local final-check reports or reference replacements to a model and
subsequently describe those same cases as unseen or held-out evaluation.
