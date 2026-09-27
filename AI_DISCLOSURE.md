# AI Assistance Disclosure — CONTINUUM

This repository was developed with AI coding assistance. The team remains
responsible for the architecture, scenario and gold-set curation, review,
safety policy, and submitted results.

## Assistance used

- Research triage and drafting of `docs/RESEARCH_REPORT.md`.
- Implementation scaffolding and refactoring for the runtime, planner, tool
  sandbox, JSONL harness edge, perception adapters, tests, and documentation.
- Test, lint, type-check, evaluation, and compatibility debugging.

## Controls and limitations

- AI-generated changes were run through the repository's deterministic tests,
  Ruff, and mypy before inclusion.
- No hidden service or credential is needed by the default path.
  `offline-fake` is explicitly a deterministic CI lane, not a claim of model
  intelligence. Real LLM backends remain optional and environment-gated.
- Optional ASR accepts only an existing local model directory and uses
  `local_files_only=True`; the default multimodal evaluation uses provided
  transcript/OCR evidence and never downloads a model.
- The runtime evaluation suites were built alongside the implementation and
  are described as regression/mechanism checks, not independent estimates.
  The frozen planner held-out split is retained separately.
- Safety decisions remain explicit in code: risk tiers, CommitGate,
  verification after uncertain mutation timeouts, idempotency keys, stale
  result rejection, and truthful timeout/retraction language.

## Reproduction

```bash
python -m pytest -q
ruff check src tests scripts
mypy src
make eval-b
```

The commands regenerate or verify:

- 246 deterministic tests;
- planner held-out fully-correct rate 0.964;
- text runtime score 100.00 vs naive runtime 54.65;
- multimodal runtime score 100.00 vs naive runtime 81.15;
- one duplicate mutation in the no-verification ablation; and
- one regretted irreversible action in the no-CommitGate ablation.

Machine-readable reports and per-scenario traces are checked in under
`reports/` so claims can be audited rather than accepted from prose.
