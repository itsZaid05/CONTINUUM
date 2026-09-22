# CONTINUUM — Verified Status (Phase 4 + Phase 5 wiring complete)

> **Current phase:** 4 — Speculation (shadow budget, metrics, cleanup) ✅ · Phase 5 comparison/preview ✅
> **Date:** 2026-09-22
> **Branch:** `arena/01a0c708-new` (supersedes merged `arena/01a0c653-new`, PR #1)
> **Version:** `0.4.0-phase4`
> **Integrity note:** the previous sandbox's `bb76337` (Phase 4, 132 tests) was **never pushed** and was lost with the sandbox. Phase 4 has been **rebuilt from the plan spec** in this checkout and is now committed + pushed, so it cannot be lost again.

---

## What is done (runnable today)

| Component | File | Tests | Status |
|-----------|------|-------|--------|
| **Contracts** — frozen Pydantic v2 schemas for all 9 steps | `src/continuum/contracts.py` | `tests/test_contract.py` (22) | ✅ Pass |
| **Versioned State** — V1→V2→V3, WAL, `merge_rapid` 300ms | `src/continuum/versioned_state.py` | `tests/test_versioned_state.py` (7) | ✅ Pass |
| **Provenance + Stale Gate** — typed vector, selective invalidate, `APPLY/DISCARD` | `src/continuum/provenance.py` | `tests/test_provenance.py` (7) | ✅ Pass |
| **Perception FAST PATH** — Layer 0 deterministic <15ms, backchannel + retract heuristics | `src/continuum/perception.py` | `tests/test_perception.py` (5) | ✅ Pass |
| **Fused Delta+Arbiter (offline)** — 1-call table+regex, 5-way, initial-request heuristic (no bogus delta on turn-0 requests) | `src/continuum/delta_arbiter.py` | `tests/test_arbiter.py` (8) + gold harness | ✅ Pass — 100% on frozen `arbiter_100.jsonl` |
| **LLM Adapter** — fused prompt, calibrate T=1.2, dense MiniLM gate, ollama/gemini/openai + env fallback | `src/continuum/llm.py` | `tests/test_llm_adapter.py` (24) | ✅ Pass |
| **Policy + CommitGate (Phase 3)** — `FREE/STAGEABLE/MUTATING/IRREVERSIBLE`, `can_execute → ALLOW/STAGE/CONFIRM_REQUIRED/BLOCK` | `src/continuum/policy.py` | `tests/test_policy.py` (6) | ✅ Pass — IRREVERSIBLE blocked w/o confirm, BLOCK under RETRACT |
| **Dialogue manager (Phase 3)** — 10 canned templates, honest retraction-after-commit, clarify-on-low-confidence | `src/continuum/dialogue.py` | `tests/test_dialogue.py` (5) + replay | ✅ Pass — `dialogue` events in every trace |
| **Effect Ledger + 10-case timeout matrix** — `sha256(v+tool+args)` idempotency, `verify_after_timeout` (no blind retry, no double-book) | `src/continuum/ledger.py` | `tests/test_ledger.py` (16) | ✅ Pass — 10/10 DoD matrix |
| **Branch Manager (Phase 4)** — SpeculationBudget `{max_shadow=2, max_depth=3, max_calls=6, allowed_levels={FREE,STAGEABLE}, pause_shadow_when_busy}`, lifecycle + `ABANDONED`, cleanup timing, reused/wasted metrics | `src/continuum/branch_manager.py` | `tests/test_branch.py` (6) + `tests/test_branch_budget.py` (6) | ✅ Pass — 3rd spawn refused, book/pay refused, busy pauses |
| **Shadow Scorer (Phase 4)** — FlowContext gate conf∈[0.55,0.72), MODIFY/ADD_CONSTRAINT only, k_eff≥1.2, top-2 hypotheses (morning/evening + history boost), `reports/shadow_scores.jsonl` | `src/continuum/shadow.py` | `tests/test_shadow.py` (10) | ✅ Pass |
| **Replay (Phase 4 wiring)** — shadow spawn → promote (reuse) / discard→cancel→cleanup (waste); honest_retract event; `dialogue` events; ablation flags `disable_stale_gate/disable_shadows` | `src/continuum/replay.py` | `tests/test_replay.py` (8) | ✅ Pass — `shadow_bangalore`: spawned 2 / promoted 1 / wasted 1 |
| **CLI** — `replay / eval-arbiter / compare (+shadow reports) / ablate / serve (uvicorn)` | `src/continuum/cli.py` | (manual + compare tests) | ✅ Pass |
| **API preview** — FastAPI `GET / /health /replay/{id} /metrics/{arbiter,comparison,shadow}` CORS=*, 0.0.0.0 | `src/continuum/api.py` | (live preview) | ✅ Pass — v0.4.0-phase4 |
| **Gold** — 100 utterances, 20/category, hash `b8920267657a` | `data/gold/arbiter_100.jsonl` | `scripts/make_gold.py` | ✅ Frozen |
| **Baseline agent (Phase 5)** — naive redo-all, independent: stale-apply / retract-ignore / double-book / fake-undo measured as defects on same scenarios | `src/continuum/baseline.py` | `tests/test_baseline.py` (6) | ✅ Pass |
| **Scenarios** — 7 deterministic traces (incl. `shadow_bangalore` `force_shadow` demo + R-02 `duplicate_result`) | `data/scenarios/*.json` | replay | ✅ 7/7 green |

**Counts:** **137 tests**, 0 failures · `ruff check src tests` ✅ · `mypy src` ✅ (17 files) · `pytest -q` <10s.

---

## Verified numbers (2026-09-22, offline-fake)

```text
Arbiter accuracy 100.00% macro-F1 1.000 (gold b8920267657a) — all 5 categories 1.00
ECE (real, 10-bin): 0.106 — fake table mildly underconfident vs 1.00 acc; reported, not hidden
Baseline naive agent vs continuum (reports/comparison.md):
  delhi_bangalore      6480ms → 1300ms  79.9%   (baseline applies stale Delhi result → wrong booking)
  analytic redo-all    2450ms → 1300ms  46.9%   (kept as baseline_analytic_ms for transparency)
  dont_book_it         2400ms → 1100ms  54.2%   (RETRACT prunes book, keeps search)
  rapid_burst          1350ms → 1350ms   0.0%
  retract_after_commit 2400ms → 2400ms   0.0%   (HONEST_RETRACT ref BLR-11:03, state V1 untouched)
  shadow_bangalore     2100ms → 1100ms  47.6%   (+ 2 shadows)
  timeout_booking      2250ms → 2250ms   0.0%   (verify → reuse)
Shadow metrics (PRD): spawned 2 → promoted 1 (reused 50.0%), discarded 1 (wasted 50.0%),
  cleanup p95 0.009ms (<150ms target), primary slowdown 4.5% (hard cap ≤5%)
Ablation: disable stale gate → 2 stale Delhi results leak into Bangalore state (bug demonstrated);
  disable shadows → 0 spawned, 0% slowdown (speculation is opt-in work, never required)
Timeout matrix: 10/10 no-double-book, COMMITTED never re-dispatched
Duplicate delivery (R-02): applied once, second ignored (duplicate_result scenario + test)
```

---

## How to verify (for judges)

```bash
git checkout arena/01a0c708-new
pip install --break-system-packages -e ".[dev]"   # or: uv sync --extra dev
pytest -q                                          # 137 passed
python -m continuum.cli replay data/scenarios/delhi_bangalore.json --trace | tail -n 30
python -m continuum.cli replay data/scenarios/shadow_bangalore.json --trace | grep -E "shadow_(spawn|promote|discard)"
python -m continuum.cli replay data/scenarios/retract_after_commit.json --trace | grep -A2 honest_retract
python -m continuum.cli eval-arbiter --gold data/gold/arbiter_100.jsonl --output reports/arbiter_accuracy.json
python -m continuum.cli compare --output reports/comparison.json    # + shadow_metrics.{json,md} + shadow_scores.jsonl
python -m continuum.cli ablate                                       # reports/ablation.{md,json}
cat reports/shadow_metrics.md
make eval    # test + lint + arbiter + compare
make demo    # FastAPI on 0.0.0.0:8000 → https://8000-*.e2b.app
```

**Expected key lines:**

```text
shadow_spawn … "label": "Bangalore morning", "score": 0.65   ← history boost (morning in state)
shadow_spawn … "label": "Bangalore evening", "score": 0.24
shadow_promote … "reused": true                                 ← speculation paid off
shadow_discard … "wasted": true, "cleanup_latency_ms": 0.01    ← bounded cost
{"event": "honest_retract", "ref": "BLR-11:03", "applied": false, "cancel_offer": true}
```

---

## Remaining polish (optional, not blockers)

- Real `GEMINI_API_KEY` eval pass (numbers already env-gated + simulated)
- Docker packaging (`PRISM_GENAI_HACKATHON_Y2026` optional per PRD)
- Demo video (script in `docs/DEMO_SCRIPT.md`)
- `merge_rapid` burst coalescing in replay (unit-tested on store; replay shows 3 patches for visibility)
