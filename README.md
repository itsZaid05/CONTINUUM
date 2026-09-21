# CONTINUUM — Interruptible Real-Time Agents
### Samsung PRISM Theme 05 • AI/ML Engineer A (Understanding, Dialogue & Evaluation)

> **One-line pitch:** *CONTINUUM keeps AI agents consistent when humans change their minds: it sorts what kind of change happened, keeps the work that's still valid, discards the rest, and prepares for likely next changes within strict safety and resource limits.*

[![Phase](https://img.shields.io/badge/phase-1%20foundation-green)](#phases)
[![Tests](https://img.shields.io/badge/tests-~65%20passed-yellow)](#quickstart)
[![Demo](https://img.shields.io/badge/demo-Delhi→Bangalore-blue)](#demo)

---

## Big Picture (9-Step Pipeline)

```
USER (text/audio/vision)
  │
  ▼
[1] PERCEPTION — FAST PATH (<200ms ACK)
  │
[2] INTENT DELTA EXTRACTOR — fused delta+category+confidence (1 LLM call)
  │
[3] INTERRUPT ARBITER — 5-way: NEW_GOAL / MODIFY / ADD_CONSTRAINT / RETRACT / NOISE
  │                    Low confidence → ask quick question
  ▼
[4] VERSIONED STATE — V1→V2→V3…, rapid bursts merged into one
  │
[5] EXECUTION / PROVENANCE GRAPH — typed vector, selective invalidation
  │
[6] POLICY + COMMITMENT — FREE / STAGEABLE / MUTATING / IRREVERSIBLE
  │
[7] BRANCH MANAGER — PRIMARY + ≤2 shadow branches, depth ≤3, READ/STAGE only
  │
[8] ASYNC TOOL EXECUTOR — search, planning, shadow work
  │
[9] RESULT & EFFECT GATE — stale check + idempotent ledger
  │
  ▼
APPLY or DISCARD → RESUME / RESPOND

Branch lifecycle: CREATED → RUNNING → SHADOW|ACTIVE → PROMOTED|INVALIDATED → CANCELLED → CLEANED_UP
                 └→ ABANDONED (non-cancellable dispatch, budget freed, stale gate discards late result)
```

What each new piece does is documented in `docs/ARCHITECTURE_A.md`.

---

## Quickstart (2 commands — for judges)

```bash
git clone https://github.com/itsZaid05/new.git && cd new
git checkout arena/01a0c653-new

# install (uv preferred, pip fallback)
pip install --break-system-packages -e ".[dev]"   # or: uv sync --extra dev

# run everything offline-fake (no network, deterministic, <10s)
python -m pytest -q
python -m continuum replay data/scenarios/delhi_bangalore.json --trace
python -m continuum eval-arbiter --gold data/gold/arbiter_100.jsonl --output reports/arbiter_accuracy.json
python -m continuum compare --output reports/comparison.json
cat reports/comparison.md
```

Expected (Phase 1, offline-fake):
- `pytest -q` — 50+ tests, 0 failures
- `replay delhi_bangalore` — shows V1→V2 (Bangalore) → invalidates Delhi search, reuses morning constraint, discards stale result
- `eval-arbiter` — accuracy 1.00, macro-F1 1.00 on frozen gold (offline-fake); real LLM ~0.88+
- `compare` — CONTINUUM 30-45% faster than baseline redo-all on Delhi scenario

---

## Implementation Plan & Research

| Doc | What |
|-----|------|
| `docs/RESEARCH_REPORT.md` | 40+ sources (RECAP, OODA-Tool, YC W26, FlowContext, prism_rt, FreshCtx, NADST, CLINC150/BANKING77, LiveKit). What we borrowed, what we close. |
| `docs/ARCHITECTURE_A.md` | Full Engineer A spec — Perception fast path, fused delta+arbiter, versioned state, provenance vector, policy, shadow scoring, evaluation. |
| `docs/IMPLEMENTATION_PLAN.md` | Phase-by-phase (1-5) with DoD, metrics, build order, cut line. |

**Build order (cut from bottom if time short):**
1. ✅ **Phase 1 — Foundation:** versioned state, provenance, stale gate, Delhi→Bangalore demo *(this checkout)*
2. ⏳ **Phase 2 — Understanding:** arbiter with all 5 categories + "don't book it" retraction *(next)*
3. ⏳ **Phase 3 — Safety:** risk levels + effect ledger for timeouts
4. ⏳ **Phase 4 — Speculation:** shadow branches with budget + cleanup
5. ⏳ **Phase 5 — Comparison:** baseline vs CONTINUUM + metrics

---

## Scenarios (Deterministic Replay)

```bash
python -m continuum replay data/scenarios/delhi_bangalore.json --trace  # core demo
python -m continuum replay data/scenarios/dont_book_it.json --trace     # retraction keeps search
python -m continuum replay data/scenarios/retract_after_commit.json --trace  # honest "already booked"
python -m continuum replay data/scenarios/timeout_booking.json --trace  # verify after timeout, no double-book
python -m continuum replay data/scenarios/rapid_burst.json --trace     # merged burst
```

Each scenario is stepped-clock (`at_ms`) — user turns + tool completions arrive concurrently, as Theme 05 requires.

---

## CLI Reference

```bash
python -m continuum replay <scenario.json> [--backend offline-fake|ollama|gemini] [--trace] [--output out.json]
python -m continuum eval-arbiter --gold data/gold/arbiter_100.jsonl --output reports/arbiter_accuracy.json [--backend ...]
python -m continuum compare --output reports/comparison.json
python -m continuum serve  # placeholder for Phase 5 FastAPI
```

---

## Metrics (Phase 1)

| Metric | What | Target |
|--------|------|--------|
| Arbiter accuracy (frozen 100) | 5-way classification, macro-F1 | ≥0.88 (1.00 offline-fake) |
| Fast ACK latency | Perception Layer 0 | p95 <200ms |
| Delhi→Bangalore wall time | vs baseline redo-all | ≥35% saved (reuse search) |
| Branch cleanup time | CANCELLED → CLEANED_UP | p95 <150ms |
| Timeout correctness | verify after UNKNOWN | 100% (no double-book) |

Reports land in `reports/` — `arbiter_accuracy.json/.md`, `comparison.json/.md`.

---

## Project Layout

```
src/continuum/
  contracts.py        # Pydantic v2 — single source of truth
  perception.py       # FAST PATH (Layer 0 deterministic)
  delta_arbiter.py    # fused extractor + arbiter (offline-fake table + future LLM)
  versioned_state.py  # V1→V2→V3 + WAL + merge
  provenance.py       # typed provenance vector + selective invalidation + stale gate
  policy.py           # FREE/STAGEABLE/MUTATING/IRREVERSIBLE
  branch_manager.py   # speculation budget, lifecycle
  ledger.py           # effect ledger + verify after timeout
  replay.py           # stepped-clock deterministic replay
  cli.py              # typer CLI
data/
  gold/arbiter_100.jsonl           # frozen 100, 20 per category
  scenarios/*.json                 # Delhi→Bangalore, retractions, timeout, burst
tests/  # contract + unit + integration
docs/   # research, architecture, plan
```

---

## Why This Wins Prism (Theme 05)

- **Deterministic & reproducible** — judges rerun in 60s, not trust a video
- **Honest safety** — retraction-after-commit says "already booked" and offers cancel; timeout verify never double-books
- **Latency is a number** — fast_ack histogram, one-call fused delta saves 800ms vs naive 2-call
- **Borrowed brilliance** — RECAP rewriting, FlowContext scheduler, prism_rt WAL, FreshCtx dependency graph, OODA separation

---

## Acknowledgments

Reuse and learnings from AccessFlow, `prism_rt` (itsramhere), FlowContext, TriFusion, RECAP (Megagon Labs), NADST, FreshCtx, Parallax, OODA-Tool, Cost-Aware Speculative Execution, LiveKit/Agora interruption handling.
