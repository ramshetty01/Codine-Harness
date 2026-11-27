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

Scope remains partial for Q1 and R1: this is one Python module/package with five
repair categories, not broad real-repository or multi-language coverage. The
[escape-string-regexp repository](https://github.com/sindresorhus/escape-string-regexp)
was also inspected (HEAD `a7f7dabaf336bda98d2166848af15d9168b10bcf`). No Node image was
available locally, so no JavaScript fixture is claimed as verified and none is
silently downloaded. Next coverage needs another upstream project, an explicitly
prepared Node+Python image for the current bootstrap, language-specific builds,
and bounded agent-driven evaluation separate from reference repair validation.

Validation performed: all five broken/reference pairs passed their intended
Docker checks (20 isolated command executions), with zero inference requests.
Offline unit tests additionally reject source tampering, unsafe paths, vacuous
baselines, identical public/final commands and timeout-as-success mistakes.
