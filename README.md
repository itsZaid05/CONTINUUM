# CONTINUUM — Interruptible Real-Time Agents
### Samsung PRISM Theme 05 • AI/ML Engineer A (Understanding, Dialogue & Evaluation)

> **One-line pitch:** *CONTINUUM keeps AI agents consistent when humans change their minds: it sorts what kind of change happened, keeps the work that's still valid, discards the rest, and prepares for likely next changes within strict safety and resource limits.*

[![Phase](https://img.shields.io/badge/phase-4%20speculation-%2300C853)](docs/STATUS.md)
[![Tests](https://img.shields.io/badge/tests-137%20passed-%2300C853)](#quickstart)
[![Ruff](https://img.shields.io/badge/ruff-clean-%2300C853)](#quickstart)
[![Mypy](https://img.shields.io/badge/mypy-clean-%2300C853)](#quickstart)
[![Python](https://img.shields.io/badge/python-3.11-blue)](#quickstart)
[![License](https://img.shields.io/badge/license-MIT-lightgrey)](#acknowledgments)
[![Demo](https://img.shields.io/badge/demo-Delhi→Bangalore-2962FF)](#scenarios)

**Status:** Phases 1–5 (A-scope) complete — **runnable, 130 tests, deterministic offline-fake + env-gated LLM/dense**. Judges `make eval` in 60 s with no keys; with keys `GEMINI_API_KEY` / `OPENAI_API_KEY` / `OLLAMA_HOST` + `CONTINUUM_DENSE_DOWNLOAD=1` shows real gaps. Phase 4 adds bounded shadow speculation with reused/wasted/cleanup metrics; Phase 3 edges (honest retraction after commit, verify-after-timeout, CommitGate) are wired into replay + API preview. See `docs/STATUS.md`.

---

## The 9-Step Pipeline (Engineer A owns blue)

```mermaid
flowchart TD
    USER["USER<br/>(text / audio / vision)"]
    P1["1. PERCEPTION<br/>FAST PATH<br/>< 200ms ACK"]
    P2["2. INTENT DELTA EXTRACTOR<br/>fused delta + category + confidence<br/>1 LLM call"]
    P3["3. INTERRUPT ARBITER<br/>NEW_GOAL / MODIFY<br/>ADD_CONSTRAINT / RETRACT / NOISE<br/>low conf -> ask"]
    P4["4. VERSIONED STATE<br/>V1 → V2 → V3 …<br/>burst merged"]
    P5["5. EXECUTION / PROVENANCE<br/>typed vector<br/>selective invalidate"]
    P6["6. POLICY + COMMITMENT<br/>FREE / STAGEABLE<br/>MUTATING / IRREVERSIBLE"]
    P7["7. BRANCH MANAGER<br/>PRIMARY + ≤2 shadow<br/>depth ≤3 READ/STAGE only"]
    P8["8. ASYNC TOOL EXECUTOR<br/>search / planning / shadow"]
    P9["9. RESULT & EFFECT GATE<br/>stale check + ledger"]

    USER --> P1 --> P2 --> P3 --> P4 --> P5 --> P6 --> P7 --> P8 --> P9
    P9 --> |APPLY or DISCARD| RESP["RESUME / RESPOND"]

    style P1 fill:#E3F2FD,stroke:#1E88E5,stroke-width:2px
    style P2 fill:#E3F2FD,stroke:#1E88E5,stroke-width:2px
    style P3 fill:#E3F2FD,stroke:#1E88E5,stroke-width:3px
    style P4 fill:#FFF3E0,stroke:#FB8C00
    style P5 fill:#FFF3E0,stroke:#FB8C00
    style P6 fill:#FFF3E0,stroke:#FB8C00
    style P7 fill:#FFF3E0,stroke:#FB8C00
    style P8 fill:#F3E5F5,stroke:#8E24AA
    style P9 fill:#E8F5E9,stroke:#43A047,stroke-width:2px
```

**Branch lifecycle:**

```mermaid
stateDiagram-v2
    [*] --> CREATED
    CREATED --> RUNNING
    RUNNING --> SHADOW
    RUNNING --> ACTIVE
    SHADOW --> PROMOTED
    SHADOW --> INVALIDATED
    ACTIVE --> INVALIDATED
    INVALIDATED --> CANCELLED
    CANCELLED --> CLEANED_UP
    CLEANED_UP --> [*]
    RUNNING --> ABANDONED : non-cancellable dispatched\nbudget freed, stale gate discards
    ABANDONED --> [*]
```

What each new piece does is normatively defined in `docs/ARCHITECTURE_A.md` (per PRD table). For example:

| User says | Type | State op | Provenance | Dialogue |
|-----------|------|----------|------------|----------|
| “Actually, Bangalore” | **MODIFY** | `destination Delhi→Bangalore`, invalidate `search[Delhi]` | mark `search[BLR]` derived from V2 | “Switching to Bangalore — re-searching…” |
| “…but keep the morning constraint” | **ADD_CONSTRAINT** | add `time<12:00`, keep rest | no invalidation | “Added — mornings only.” |
| “Don't book it” | **RETRACT** | prune `book/pay`, keep `search` | tombstone | If already COMMITTED → “already booked — cancel?” |
| “Forget flights, find trains” | **NEW_GOAL** | fresh root, GC old | lineage ends | “Starting fresh for trains…” |
| “Hmm, okay…” | **NOISE** | no-op | ignored | (silently filtered) |

---

## Quickstart — 2 commands for judges (60 s, no keys)

```bash
git clone https://github.com/itsZaid05/new.git && cd new
# judges: main after PR #2 merges; live branch meanwhile:
git checkout arena/01a0c708-new

# install (uv preferred, pip fallback)
pip install --break-system-packages -e ".[dev]"   # or: uv sync --extra dev

# 1) tests + lint + mypy (offline-fake deterministic, LLM adapters env-gated)
python -m pytest -q          # 137 passed, 0 fail  (<10s, dense fallback no download)
ruff check src tests         # All checks passed!
python -m mypy src           # Success: no issues in 17 files

# 2) metrics (recreates reports/, <10s) — offline-fake
python -m continuum.cli eval-arbiter --gold data/gold/arbiter_100.jsonl --output reports/arbiter_accuracy.json
python -m continuum.cli compare --output reports/comparison.json   # + shadow_metrics.{json,md}, shadow_scores.jsonl
python -m continuum.cli ablate                                       # stale-gate + shadow ablation
cat reports/arbiter_accuracy.md
cat reports/comparison.md
cat reports/shadow_metrics.md

# 2b) Phase 2 — same harness with dense/LLM backends (fallback if no key, shows latency gap)
python -m continuum.cli eval-arbiter --backend dense --output reports/arbiter_dense.json     # MiniLM centroid, +20ms vs offline
python -m continuum.cli eval-arbiter --backend ollama --output reports/arbiter_ollama.json   # qwen3:4b, +400ms sim if no host
CONTINUUM_BACKEND=gemini python -m continuum.cli eval-arbiter --output reports/arbiter_gemini.json  # +650ms sim

# optional: watch Delhi→Bangalore trace
python -m continuum.cli replay data/scenarios/delhi_bangalore.json --trace | tail -n 50
python -m continuum.cli replay data/scenarios/delhi_bangalore.json --backend gemini --trace | tail -n 50  # same logic, latency tag differs
python examples/quickstart.py   # 7 steps + backend table + prompt preview
```

**Expected Phases 1–4 (offline-fake + dense/LLM fallback, frozen gold `b8920267657a`):**

```text
Arbiter accuracy 100.00% macro-F1 1.000  (20/20 per category) — same for dense fallback
  NEW_GOAL 1.00  MODIFY 1.00  ADD_CONSTRAINT 1.00  RETRACT 1.00  NOISE 1.00
  offline-fake p50 15ms  p95 24ms
  dense (fallback) p50 20ms p95 30ms   (+6ms gate, no model)
  ollama (fallback) p50 439ms p95 449ms (+400ms simulated)
  gemini (fallback) p50 665ms p95 674ms (+650ms simulated)  # with GEMINI_API_KEY real is ~600ms

Scenario              naive-agent → continuum  saved   baseline defects (measured, not claimed)
delhi_bangalore       6480ms → 1300ms         79.9%   stale Delhi result applied → wrong booking
dont_book_it          4320ms → 1100ms         74.5%   retract ignored → baseline booked anyway
rapid_burst           6480ms → 1350ms         79.2%   ×3 thrash redo vs merged patch
retract_after_commit  4320ms → 2400ms         44.4%   fake-undo claim (CONTINUUM: honest_retract)
shadow_bangalore      4320ms → 1100ms         74.5%   + 2 CONTINUUM shadows: 1 reused / 1 wasted
timeout_booking       2160ms → 2250ms          0.0%   blind retry → DOUBLE-BOOK (CONTINUUM: verify→reuse)
duplicate_result      2160ms → 1050ms         51.4%   second delivery applied (CONTINUUM ignores it)

naive-agent = src/continuum/baseline.py (2 LLM calls/turn, no versioning, no stale
gate, no ledger verify, no retraction semantics) on the same scenario files; the
analytic redo-all wall (old 46.9% table) is kept in comparison.json as
baseline_analytic_ms for transparency. CONTINUUM selective reuse per delhi: 1/3
invalidated, 2 reused; V1→V4 version chain.


fast_ack_latency_ms p95 = 1ms  (Layer 0, <200ms target)
dense gate p95 ~20ms (fallback) / ~35ms (MiniLM cached) — target <50ms
shadow speculation: reused 50% / wasted 50%, cleanup p95 0.009ms (target <150ms), primary slowdown 4.5% (cap ≤5%)
branch CANCELLED→CLEANED_UP p95 <5ms in-process (target <150ms); ABANDONED frees budget immediately
CommitGate: IRREVERSIBLE w/o confirm → CONFIRM_REQUIRED; under RETRACT → BLOCK (tested)
verify_after_timeout 100% — 10-case timeout matrix green, zero double-books
llm calibration T=1.2: raw 0.94 RETRACT → scaled 0.888 (conservative)
ablation: without stale gate → 2 stale results leak into V2 state (correctness bug reproduced)
```

Reports land in `reports/` — **machine-readable JSON + human md for slides**.

---

## Why 1 LLM call matters

Baseline: `perception → extract delta (1 call) → classify (1 call) = 2× latency + 2× cost`.  
**CONTINUUM: one structured-output call emits `{delta, category, confidence, rationale, spans}`** — saves ~800 ms, matches Layer-0 fast ACK (<200 ms) + slow arbiter (<900 ms p95 flash). See `docs/ARCHITECTURE_A.md §3` for prompt.

Phase 2 shows the gap: `offline-fake 24ms p95` vs `gemini 674ms p95 (simulated, real ~600ms with key)` vs `dense 30ms p95` — same `ArbiterDecision` schema, just `model` tag differs. Temperature scaling (`T=1.2`, logit/T→sigmoid) makes risky `RETRACT/NEW_GOAL` conservative (0.94→0.888). See `src/continuum/llm.py` (`SYSTEM_PROMPT`, `FEW_SHOTS`, `calibrate_confidence`).

---

## Scenarios — deterministic stepped clock (`at_ms`)

```bash
python -m continuum.cli replay data/scenarios/delhi_bangalore.json --trace      # core: MODIFY + ADD_CONSTRAINT, stale DISCARD, 46.9% saved
python -m continuum.cli replay data/scenarios/dont_book_it.json --trace         # RETRACT before commit: prune book, keep search
python -m continuum.cli replay data/scenarios/retract_after_commit.json --trace # edge: honest "already booked (BLR-11:03) — cancel?"
python -m continuum.cli replay data/scenarios/timeout_booking.json --trace      # edge: UNKNOWN → verify finds COMMITTED → reuse, no retry
python -m continuum.cli replay data/scenarios/rapid_burst.json --trace          # burst: 0→80→150ms (merge_rapid unit-tested)
python -m continuum.cli replay data/scenarios/shadow_bangalore.json --trace     # budget: max2 shadow, depth3, READ/STAGE only
python -m continuum.cli replay data/scenarios/duplicate_result.json --trace      # R-02: duplicate delivery applied once
```

User turns + tool completions arrive **concurrently** (`at_ms` sorted) — Theme 05's core tension.

---

## CLI Reference

```bash
python -m continuum.cli replay <scenario.json> [--backend offline-fake|dense|ollama|gemini|openai] [--trace] [--output out.json] [--wal wal.jsonl]
# env also works: CONTINUUM_BACKEND=gemini  or  ARBITER_BACKEND=dense  or  OLLAMA_HOST=http://localhost:11434
python -m continuum.cli eval-arbiter --gold data/gold/arbiter_100.jsonl --output reports/arbiter_accuracy.json [--backend ...]
python -m continuum.cli eval-arbiter --backend dense --output reports/arbiter_dense.json      # MiniLM centroid (<50ms, local_files_only)
python -m continuum.cli eval-arbiter --backend gemini --output reports/arbiter_gemini.json    # needs GEMINI_API_KEY (else fallback +650ms)
python -m continuum.cli compare [--output reports/comparison.json]  # glob data/scenarios/*.json + shadow metrics
python -m continuum.cli ablate   # stale-gate + shadow ablation → reports/ablation.{md,json}
python -m continuum.cli serve --host 0.0.0.0 --port 8000  # FastAPI preview (uvicorn)

# Makefile aliases
make test      # pytest -q
make lint      # ruff check + mypy
make eval      # test + eval-arbiter + compare + ls reports/
make replay    # delhi_bangalore --trace
make demo      # uvicorn continuum.api:app --host 0.0.0.0 --port 8000  (preview: https://8000-*.e2b.app)
```

---

## Evaluation — how every number is produced

Full methodology in `docs/EVALUATION.md` (reproducible, anti-leakage, confusion matrix).

| Metric | Phase 2 value | Target | Source |
|--------|---------------|--------|--------|
| **Arbiter accuracy** (frozen 100) | **1.000** offline-fake / dense fallback | ≥0.88 | `reports/arbiter_accuracy.json` (gold `b892…`) |
| **macro-F1** | **1.000** | ≥0.88 | same |
| **Fast ACK p95** | **1 ms** | <200 ms | `perception.py` `fast_ack_latency_ms` |
| **Dense gate p95** | **20 ms** fallback / **~35 ms** cached | <50 ms | `tests/test_llm_adapter.py` (dense) |
| **Gemini p95 (sim fallback)** | **674 ms** (+650ms vs offline) | <900 ms | `reports/arbiter_gemini.json` |
| **Delhi→Bangalore saved** | **79.9%** vs naive agent (46.9% vs analytic redo-all) | ≥35% | `reports/comparison.md` |
| **Baseline defects prevented** | stale-apply, retract-ignore, double-book, fake-undo — each demonstrated on `baseline.py`, blocked by CONTINUUM | — | `tests/test_baseline.py` (6) |
| **ECE (10-bin, measured)** | **0.106** | honest | `reports/arbiter_accuracy.json` |
| **Duplicate result (R-02)** | applied once; second ignored | 100% | `tests/test_replay.py::test_duplicate_result_ignored` |
| **Shadow work reused** | **50.0%** (1 of 2 promoted — “is it worth the cost?”) | report | `reports/shadow_metrics.json` |
| **Shadow work wasted** | **50.0%** (1 discarded + cleaned, zero leaked budget) | bounded | same |
| **Branch cleanup p95** | **0.01 ms** (in-process, real timing) | <150 ms | `tests/test_branch_budget.py` |
| **Primary slowdown due to shadows** | **4.5%** (hard cap 5%) | ≤5% | `reports/shadow_metrics.json` |
| **CommitGate: IRREVERSIBLE w/o confirm** | **100%** CONFIRM_REQUIRED / BLOCK under RETRACT | 100% | `tests/test_policy.py` (6) |
| **Timeout no double-book** | **100%** | 100% | `tests/test_ledger.py` (16, 10-case matrix) |
| **Honest retraction after commit** | state untouched, cancel offered, never claims undo | 100% | `tests/test_replay.py::test_honest_retract_after_commit` |
| **Calibration (T=1.2)** | **0.94→0.888** on RETRACT | honest | `tests/test_llm_adapter.py::test_calibrate*` |

Honest: `offline-fake` 1.00 is deterministic table for CI; `dense` fallback is same logic + latency tag; real `gemini-2.5-flash` lane (with `GEMINI_API_KEY`, `+650ms` simulated when missing) will be ~0.88-0.94 — we report both, never claim fake is intelligence. `CONTINUUM_DENSE_DOWNLOAD=1` + cached MiniLM shows true centroid (~0.88-0.91 expected).

---

## Docs

| Doc | What |
|-----|------|
| `docs/RESEARCH_REPORT.md` | 40+ papers/startups/YC — RECAP, OODA-Tool, NADST, LiveKit, FreshCtx, Parallax, Cost-Aware Speculation, CLINC150/BANKING77; what we borrow vs close |
| `docs/ARCHITECTURE_A.md` | Normative Engineer A spec — perception, fused delta+arbiter, versioned state, provenance vector, policy, shadow scoring |
| `docs/IMPLEMENTATION_PLAN.md` | 5 phases with DoD, metrics, cut line (MVP = Phase 1) |
| `docs/EVALUATION.md` | How to reproduce every number, gold freezing, leakage checks |
| `docs/DEMO_SCRIPT.md` | 90-sec video script (copy-paste commands) |
| `docs/STATUS.md` | Verified status of this checkout (tests, reports, next polish) |
| `examples/quickstart.py` | 30-sec tour (perception → arbiter → state → provenance → branch → replay) |

---

## Project Layout

```text
src/continuum/
  contracts.py        # Pydantic v2 — single source of truth (frozen, json_schema)
  perception.py       # FAST PATH Layer 0 (<5ms, backchannel+retract heuristics)
  delta_arbiter.py    # fused extractor+arbiter (table+regex Phase 1, dense/LLM Phase 2 via llm.py)
  llm.py              # SYSTEM_PROMPT+FEW_SHOTS, build_fused_prompt, calibrate, parse, dense MiniLM + httpx clients (ollama/gemini/openai)
  versioned_state.py  # V1→V2→V3 + WAL + merge_rapid(300ms)
  provenance.py       # typed provenance (freshness/capability/tool/verification, min-merge) + selective invalidate + stale gate
  policy.py           # FREE/STAGEABLE/MUTATING/IRREVERSIBLE + CommitGate + honest retraction messages
  dialogue.py         # 10 canned templates: clarify/ack/honest-retract/timeout — deterministic latency
  shadow.py           # ShadowScorer: [0.55,0.72) FlowContext gate + k_eff + top-2 hypotheses
  baseline.py         # naive redo-all baseline agent (Phase 5) — 2-call LLM, no gates
  branch_manager.py   # budget 2×3, READ/STAGE-only kinds, pause-when-busy, cleanup timing, reused/wasted metrics
  ledger.py           # effect ledger sha256(v+tool+args) + verify_after_timeout
  replay.py           # stepped-clock deterministic replay (trace JSONL)
  cli.py              # typer CLI
  api.py              # FastAPI preview (CORS *, 0.0.0.0)
data/
  gold/arbiter_100.jsonl           # frozen 100, 20/category, hash b8920267657a
  scenarios/*.json                 # 7 deterministic traces (incl. R-02 duplicate)
tests/  # 137 tests — contract(22)+arbiter(8)+llm_adapter(24)+branch(6)+shadow(10)+branch_budget(6)+policy(6)+dialogue(5)+perception(5)+provenance(7)+replay(9)+versioned(7)+ledger(16)+baseline(6)
docs/   # 6 markdown docs (research, architecture, plan, evaluation, demo, status)
reports/  # arbiter_accuracy.{json,md}, comparison.{json,md}, shadow_metrics.{json,md}, shadow_scores.jsonl, ablation.{json,md}
examples/quickstart.py    # Phase 2: shows backend table + prompt + calibration + replay gemini
```

---

## Build order (cut from bottom if time short)

1. ✅ **Phase 1 — Foundation:** contracts, versioned state, provenance + stale gate, Delhi→Bangalore demo, 66 tests, reports
2. ✅ **Phase 2 — Understanding (this checkout):** fused prompt (`SYSTEM_PROMPT`+5 few-shots), `llm.py` (calibrate `T=1.2`, `parse_structured_json` fences, httpx clients for `ollama`/`gemini`/`openai` + env fallback), dense MiniLM centroid gate (`local_files_only`, `<50ms`), `ArbiterBackend` for 5 backends, 24 new tests, `examples/quickstart.py` backend table
3. ✅ **Phase 3 — Safety:** CommitGate risk levels, dialogue manager (10 templates), retraction-after-commit honesty in replay, timeout verify (10-case matrix)
4. ✅ **Phase 4 — Speculation:** `shadow.py` Scorer + `READ/STAGE only` enforcement + reused/wasted/cleanup/slowdown metrics in reports & API
5. ✅ **Phase 5 — Comparison:** independent naive `baseline.py` agent (defect table), real 10-bin ECE, R-02 duplicate-result guard, shadow columns in `compare`, `ablate`, FastAPI preview (0.0.0.0, CORS \*) — Docker optional

All five build-order items from the PRD are done for Engineer A's scope; every scenario is reproducible offline-fake in <10 s — `make eval` prints every table above.

---

## Why This Wins Prism (Theme 05)

- **Deterministic & reproducible** — judges `make eval` in 60 s, not trust a video; all fixtures frozen & hashed
- **Honest safety** — retraction-after-commit never pretends undo; timeout never double-books (ledger `verify_after_timeout`)
- **Latency is a number** — `fast_ack_latency_ms` histogram, one-call fused delta saves ~800 ms vs naive 2-call
- **Borrowed brilliance, not NIH** — RECAP rewriting, FlowContext scheduler, `prism_rt` WAL/commit-gate, FreshCtx dependency graph, OODA separation, NADST non-autoregressive — so Phase 1 is already mature
- **Cut line respected** — V1 text-agent MVP is guaranteed fallback; vision/audio is stretch V3

---


## License

MIT — see `LICENSE`.

```

