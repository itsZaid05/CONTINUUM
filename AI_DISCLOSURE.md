# AI Assistance Disclosure — CONTINUUM (Engineer A)

Required at submission by the hackathon convention (as enforced in sibling repos).

## What AI was used for
- **Research triage** — 40+ papers/repos/YC sources screened and summarized into
  `docs/RESEARCH_REPORT.md` (every borrow is attributed there and in README Acknowledgments).
- **Drafting** — code scaffolding for phases 1–5 following `docs/IMPLEMENTATION_PLAN.md`;
  all designs are Engineer A's stated architecture (9-step pipeline, arbiter taxonomy,
  speculation budget), not invented by the assistant.
- **Verification** — every number in `reports/` is produced by deterministic commands a
  human (or judge) can re-run in <10s: `pytest -q`, `continuum eval-arbiter`,
  `continuum compare`, `continuum ablate`. No metric was written by hand.

## What AI was NOT allowed to do
- No unreviewed merge: all phases went through review + `git push` + PR #2.
- No hidden stubs presented as results: `offline-fake` is labeled as a deterministic
  table (CI lane) in README, STATUS, and every report; real-model lanes are env-gated.
- Lost-work incident (sandbox commit unpushed) was disclosed and rebuilt from spec,
  not papered over — see `docs/STATUS.md` integrity note.

## Human ownership
- Intent taxonomy, gold-set utterances (100, hand-written post-cutoff), scenario design,
  safety rules (READ/STAGE-only speculation, honest retraction), and the metrics story
  are authored/curated by the team; the assistant implemented and tested to spec.
