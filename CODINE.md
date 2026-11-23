# Codine project guidance

Codine is an educational Python terminal coding harness. Keep the model adapter, agent loop, tool policy, session storage, and terminal rendering separate.

- Read relevant code before editing. Explain the smallest change that fixes the observed problem.
- Validate with `.venv/bin/python main.py --check` and `.venv/bin/python -m unittest discover -s tests -v`.
- Run `.venv/bin/python -m codine.evals --offline` when changing the agent loop or grader.
- Offline scripted exercises verify infrastructure, not live model solve rate. Label results accurately.
- Do not run `tests/live_smoke.py` or any live model request during the current offline development phase. Paid evaluations are a separate final stage.
- Preserve unrelated user changes, especially the current `example.py` edit.
- Keep keys, research downloads, sessions, and evaluation reports out of commits.
- Never treat a model's final answer as proof that a test passed. Report actual checks and limitations.
- Changes to spending, permissions, or the grader need regression tests; do not weaken them to improve a score.

The detailed roadmap and documented deferrals are in `docs/DEVELOPMENT_PLAN.md`.
