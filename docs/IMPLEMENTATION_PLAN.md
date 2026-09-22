# CONTINUUM — Phase-by-Phase Implementation Plan
## AI/ML Engineer A (Understanding, Dialogue & Evaluation) — Samsung PRISM Theme 05
### Author: AI/ML Engineer A | Date: 2026-09-21 | Stack: Python 3.11, Pydantic v2, uv, pytest

> **Build philosophy:** Pick a *small task at once* and do it at its best. Each phase is independently runnable, demoable, and committable — judges can `git checkout phase-X && make eval` and see a number move.

---

## 0. Repository Conventions (applies to all phases)

```
new/
├── pyproject.toml            # uv, requires-python ==3.11
├── uv.lock
├── Makefile                  # baseline, eval, demo, ablate
├── src/continuum/
│   ├── contracts.py          # Pydantic schemas — single source of truth
│   ├── perception.py         # Fast Path
│   ├── delta_arbiter.py      # fused extractor + arbiter
│   ├── versioned_state.py
│   ├── provenance.py
│   ├── dialogue.py
│   ├── evaluation/           # harness
│   └── replay.py             # deterministic replay CLI
├── data/
│   ├── gold/arbiter_100.jsonl
│   └── scenarios/*.json      # deterministic traces
├── tests/                    # pytest, categories A–H
├── reports/                  # machine-readable JSON + md for judges
├── docs/
│   ├── RESEARCH_REPORT.md    # this doc's companion
│   ├── ARCHITECTURE_A.md
│   └── EVALUATION.md
└── examples/
```

- **Contracts first.** No code outside `contracts.py` until schemas are frozen and `tests/test_contract.py` passes.
- **Offline-fake default.** No network in CI. Adapters: `offline-fake` (scripted JSON) → `ollama` (local) → `gemini` (cloud). Flag: `--backend`.
- **Stepped clock + seeded RNG** for deterministic replay (borrow from `prism_rt`).
- **Commit after each phase:** tag `phase-N-a-*`.

Cut line (if time short): Phases 1-3 are **MVP that wins**; Phase 4 makes it defensible; Phase 5 is polish.
Slides: MVP = "versioned state, provenance, stale gate, Delhi→Bangalore demo" as PRD says.

---

## Phase 1 — Foundation: Contracts, Versioned State, Provenance, Stale Gate + Delhi→Bangalore Skeleton

**Goal:** Independently runnable skeleton that proves the *core Theme 05 problem*: async streaming dialogue where user utterances + tool results arrive concurrently; interruption invalidates dependents; stale gate discards.

**Duration:** 2–3 days | **Priority:** P0 (cannot cut)

### 1.1 What you build (small tasks, each PR-able)

#### Task 1.1.A — Contracts (`src/continuum/contracts.py`)
- Schemas (Pydantic v2, frozen, json_schema):
  - `EvidenceSpan`, `PerceptionOutput`
  - `Delta`, `ArbiterDecision` (5 categories + confidence)
  - `StateVersion`, `VersionedStateOp`
  - `Provenance`, `ExecutionNode`, `EffectRecord`
  - `BranchState` enum: CREATED→RUNNING→SHADOW|ACTIVE→PROMOTED|INVALIDATED→CANCELLED→CLEANED_UP (plus ABANDONED for non-cancellable)
  - `RiskLevel`: FREE | STAGEABLE | MUTATING | IRREVERSIBLE
- Validation: `pytest tests/test_contract.py` — 22 tests (borrow AccessFlow's contract test style).
- **Done when:** `make test` 22/22, `python -m continuum.replay --help` prints.

#### Task 1.1.B — Versioned State (`versioned_state.py`)
- `VersionedStore`:
  - `create_initial(state: dict) -> StateVersion` (V1)
  - `patch(base_version, delta, category) -> StateVersion` (V2)
  - `merge_rapid(deltas: list) -> StateVersion` (coalesce <300ms burst)
  - `history()`, `get(version)`, `current()`
  - WAL append-only log (JSONL) for durability (simplified: file write; real would be SQLite).
- Config: `MERGE_WINDOW_MS=300`, `MAX_VERSIONS=1000`.
- Tests: 10 unit tests (rapid merge, parent chain, tombstone).

#### Task 1.1.C — Provenance Graph + Stale Gate (`provenance.py`)
- `ProvenanceGraph`:
  - `add_node(node: ExecutionNode)` (records `based_on`)
  - `invalidate_affected(current_version) -> list[id]` (selective invalidation by dependents() map)
  - `is_stale(node, current_version) -> bool`
  - `result_gate(node, result) -> APPLY|DISCARD` (stale check version + effect ledger)
- Dependents map (hardcoded MVP, later learned):
  ```
  MODIFY destination → {search, filter, price}
  MODIFY dates → {search, price}
  ADD_CONSTRAINT time → {filter}
  RETRACT booking → {book, pay}
  NEW_GOAL → all
  NOISE → none
  ```
- Tests: 8 tests (including timeout-verify mock).

#### Task 1.1.D — Delhi → Bangalore Demo Scenario (no LLM yet)
- `data/scenarios/delhi_bangalore.json`:
  ```json
  {"turns": [
    {"user": "Book Delhi flights for next Monday morning", "state": "V1: destination=Delhi"},
    {"tool": "search_flights[Delhi] started"},
    {"user": "Actually, Bangalore", "delta": "destination Delhi→Bangalore"},
    {"expect": "invalidate search[Delhi], start search[Bangalore], keep morning constraint"}
  ]}
  ```
- `replay.py`: deterministic stepped-clock replay that:
  - ingests user turns + tool completions in timestamp order,
  - applies hardcoded delta (mock arbiter → MODIFY),
  - shows `V1→V2→V3`, invalidated nodes, reused nodes,
  - prints `Baseline wall time: 2400ms | CONTINUUM: 1400ms (42% reuse)`.
- CLI: `uv run continuum replay scenarios/delhi_bangalore.json --trace`

### 1.2 Metrics (phase 1)
- Branch cleanup time harness (stub branches)
- Delhi→Bangalore wall time vs baseline (hardcoded)
- Version chain depth

### 1.3 DoD (Definition of Done)
- [ ] `uv sync --extra dev && uv run pytest -q` — all tests pass offline-fake
- [ ] `uv run continuum replay data/scenarios/delhi_bangalore.json` prints provenance + 42% reuse
- [ ] Stale gate discards late Delhi results after V2 (test `test_stale_discard`)
- [ ] `reports/phase1_trace.jsonl` generated and viewable
- [ ] README quickstart works for judges

### 1.4 Borrowed code
- `prism_rt`: `versioned_store.py`, `commit_gate.py`, `result_router.py` — vendor with attribution, simplify.
- FreshCtx: `DependencyGraph.invalidate()` logic.

---

## Phase 2 — Understanding: Perception Fast Path + Fused Delta/Arbiter (1 LLM Call)

**Goal:** Real language understanding that sorts every change into 5 types with calibrated confidence — the *one model call outputs BOTH* promise.

**Duration:** 3–4 days | **Priority:** P0 (core IP, cannot cut)

### 2.1 Tasks

#### Task 2.2.A — Perception Fast Path (`perception.py`)
- Layer 0: regex/backchannel lexicon (NOISE instant) + explicit cancel markers
- Layer 1: MiniLM-L6-v2 embedding centroids (precomputed on 5×20 examples), cosine threshold 0.72 → fast pre-label
- Layer 2: adapter to call LLM (offline-fake for CI)
- Interface: `async def perceive(evidence_text: str, mode: str) -> PerceptionOutput` with `fast_ack` emitted <200ms.
- Measurement: log `fast_ack_latency_ms` per turn.

#### Task 2.2.B — Fused Delta + Arbiter (`delta_arbiter.py`)
- Single prompt template (see ARCHITECTURE_A.md §3) with 5 few-shot examples, randomized order per calibration run.
- Structured output via JSON schema / Gemini function calling.
- Adapters:
  - `offline-fake`: scripted table lookup (gold answers for 100 cases) — deterministic, no network.
  - `ollama`: `qwen3:4b` local (for B-tier laptops).
  - `gemini`: `gemini-2.5-flash` (primary for demo).
- Post-processing: temp scaling (T=1.2 learned on dev split), confidence clamp.
- Method: `def arbitrate(state: StateVersion, evidence: EvidenceSpan, history) -> ArbiterDecision`
- **Hard rule:** confidence <0.7 on RETRACT/MUTATING → emit `suggested_clarification` and *do not* mutate state.

#### Task 2.2.C — Gold Set Build (`data/gold/arbiter_100.jsonl`)
- 100 utterances, 20 per category, hand-written across 6 domains (flights, restaurants, health, code, shopping, trains) — avoids RECAP leakage by writing *after* cutoff, never copying CLINC150 verbatim.
- Each line: `{"utterance":"…","category":"MODIFY","delta":{"field":"destination","old":"Delhi","new":"Bangalore"},"rationale":"user replaced city, kept dates"}`
- Create via script `scripts/make_gold.py` that stubs and then manual review; freeze hash.

#### Task 2.2.D — Wire to Versioned State
- Replace hardcoded mock with real arbiter in replay:
  - `perceive → arbitrate → if confidence gate passes → versioned_store.patch → provenance.invalidate_affected`
- Rapid-merge test: `"Delhi → actually Bangalore → but morning"` burst within 300ms coalesces.

### 2.2 Metrics
- **Arbiter accuracy** macro-F1, per-category, confusion matrix, ECE — on frozen gold 100.
- Fast ACK latency p95.
- Two-call baseline vs one-call latency saving (measured).

### 2.3 DoD
- [ ] `uv run continuum eval-arbiter --gold data/gold/arbiter_100.jsonl` → `reports/arbiter_accuracy.json` with macro-F1 ≥0.88 (offline-fake should hit 1.0; real LLM ≥0.88)
- [ ] `uv run continuum replay data/scenarios/delhi_bangalore.json --backend offline-fake` still passes but now LLM-driven
- [ ] "Don't book it" scenario: `data/scenarios/dont_book_it.json` correctly prunes `book` nodes, keeps `search`
- [ ] Clarification gate test: low-confidence RETRACT triggers question, state unchanged until next turn
- [ ] Latency histogram in report

### 2.4 Borrowed code
- RECAP advanced rewriter prompt (adapt to JSON delta+category)
- IntentGPT semantic few-shot sampler (for clarification examples)
- SetFit centroid idea

---

## Phase 3 — Dialogue: Policy Gate, Retraction After Commit, Timeout Ledger

**Goal:** Complete safety story — risk levels + effect ledger + honest retraction + timeout verification. This is what makes CONTINUUM "safe" not just "fast".

**Duration:** 2–3 days | **Priority:** P0 (judge safety questions live here)

### 3.1 Tasks

#### Task 3.3.A — Policy + Commitment Control (`policy.py`)
- Enum + registry: `FREE (search)`, `STAGEABLE (hold)`, `MUTATING (cancel/prune)`, `IRREVERSIBLE (book/pay/send)`.
- `CommitGate.can_execute(node, effect_ledger, arbiter_decision) -> ALLOW|STAGE|CONFIRM_REQUIRED`
- Tests: IRREVERSIBLE without explicit confirm → blocked.

#### Task 3.3.B — Effect Ledger + Verify-After-Timeout (`ledger.py`)
- `EffectLedger`:
  - `prepare(effect_id, args) -> EffectRecord(UNKNOWN)`
  - `commit(effect_id)`
  - `verify_after_timeout(effect_id, tool_client) -> COMMITTED|ABORTED` (queries idempotent key)
  - `idempotency_key = sha256(version + tool + normalized_args)`
- Timeout simulation: inject `tool.sleep(3s)` + user interrupt → verify path tested.
- Table for edge case handling:

| Crash point | Recovery rule |
|-------------|---------------|
| Before dispatch | retry same durable key; no authority |
| After create, before receipt | query same key; quarantine output |
| After commit, before handle | republish deterministic handle |
| After verify | return durable receipt; consumed slot forbids 2nd accept |

#### Task 3.3.C — Dialogue Manager (`dialogue.py`)
- Handles:
  - `RETRACT after COMMITTED` → honest "already booked" + cancel offer (never pretend undo)
  - `low confidence → quick question`
  - `timeout → "Checking booking status…"`
  - Branch ABANDONED (non-cancellable request already sent) → budget freed, stale gate will discard later result.
- Templates: 10 canned responses for predictable latency.

#### Task 3.3.D — Scenarios
- `dont_book_it.json` (RETRACT before commit) → prune booking, keep search → verify no extra tool call.
- `retract_after_commit.json` → ledger COMMITTED → response honesty test.
- `timeout_booking.json` → UNKNOWN → verify finds booking → reuse.

### 3.2 Metrics
- Timeout recovery correctness (10 injection tests, 100% pass mandatory)
- Retraction honesty (binary, must not claim undo of committed)
- Policy gate block rate on IRREVERSIBLE without confirm

### 3.3 DoD
- [ ] `pytest tests/test_ledger.py` 10/10 timeout cases green, no double-book
- [ ] `uv run continuum replay data/scenarios/retract_after_commit.json --backend offline-fake` prints honest response, offers cancel
- [ ] `pytest tests/test_policy.py` confirms IRREVERSIBLE blocked until confirmed
- [ ] Branch lifecycle test: `ABANDONED` branches free budget immediately

### 3.4 Borrowed code
- Parallax Chronicle snapshot idea (simplified to effect_id mapping)
- OODA-Tool Orient→Decide gating logic

---

## Phase 4 — Shadow Branches with Budget + Cleanup

**Goal:** Speculation that *pays within limits* — bounded shadow work that judges can reason about cost/benefit.

**Duration:** 2 days | **Priority:** P1 (cuttable last, but differentiates from baseline)

### 4.1 Tasks

#### Task 4.1.A — Branch Manager (`branch_manager.py`)
- Budget enforcer:
  ```python
  @dataclass
  class SpeculationBudget:
      max_shadow: int = 2
      max_depth: int = 3
      max_calls_per_shadow: int = 6
      allowed_levels: set[RiskLevel] = {FREE, STAGEABLE}  # READ/STAGE only
  ```
- API: `spawn_shadow(parent_version, hypothesis: ArbiterDecision) -> Branch | Refused`, `promote(branch_id)`, `invalidate(branch_id)`, `cleanup()` async.
- Priority: PRIMARY always preempts; shadows paused first when CPU busy (asyncio semaphore + priority queue).
- Tests: spawn 3 → 3rd refused; depth 4 → truncated.

#### Task 4.1.B — Shadow Scoring (A's logic)
- Heuristic + arbiter confidence:
  - If arbiter confidence ∈[0.55,0.72) on MODIFY → spawn shadow for top-2 hypotheses (e.g., Bangalore morning vs evening).
  - If user history shows pattern "user часто says 'morning' after city change" → boost that shadow.
  - Use speculative paper's "branching factor k_eff" to decide if worth it.
- Keep simple: rule-based for MVP, log scores to `reports/shadow_scores.jsonl`.

#### Task 4.1.C — Cleanup & `ABANDONED` Handling
- States: `CREATED→RUNNING→SHADOW|ACTIVE→PROMOTED|INVALIDATED→CANCELLED→CLEANED_UP`; `ABANDONED` for non-cancellable dispatched tools.
- `cleanup()` cancels `asyncio.Task`, releases memory tracker, logs `branch_cleanup_time_ms`.
- Measurement: p95 cleanup <150ms.

### 4.2 Metrics
- **Shadow reused %** (promoted / spawned)
- **Shadow wasted %** (discarded / spawned) + cost in tool calls
- Cleanup time distribution
- Primary slowdown due to shadows (should be ≤5%)

### 4.3 DoD
- [ ] `pytest tests/test_branch_budget.py` — budget caps enforced
- [ ] `uv run continuum replay data/scenarios/shadow_bangalore.json` shows 1 reused, 1 wasted, cleanup <150ms
- [ ] Report `reports/shadow_metrics.json` with reused/wasted breakdown

### 4.4 Borrowed code
- itsramhere V3 phase plan for branch lifecycle
- FlowContext scheduler confidence gate

---

## Phase 5 — Evaluation & Demo: Baseline vs CONTINUUM + Metrics + Polish

**Goal:** One command that convinces a judge CONTINUUM beats naive baseline on correctness, speed, safety.

**Duration:** 2–3 days | **Priority:** P1 (but *presentation* priority P0 — allocate time for video)

### 5.1 Tasks

#### Task 5.1.A — Baseline Agent (`baseline.py`)
- Naive agent: no versioning, no delta, 2 LLM calls, redo-all on every interrupt, no stale gate, no ledger verify (blind retry).
- Same scenarios run on baseline vs CONTINUUM; results side-by-side.

#### Task 5.1.B — Comparison Harness (`evaluation/comparison.py`)
- Entry: `uv run continuum compare --scenarios data/scenarios/*.json --backends offline-fake,baseline`
- Output `reports/comparison.md`:
  ```
  | Scenario | Baseline wall | CONTINUUM wall | Saved | Correct? | Double-book? |
  | delhi_bangalore | 2400ms | 1400ms | 42% | ✓ | — |
  | dont_book_it | — | — | — | ✓ (pruned) | ✗ (baseline booked) |
  | timeout | blind retry → double | verify→reuse | — | ✓ | ✗→✓ |
  | rapid_burst | thrash ×3 | merge ×1 | 60% | ✓ | — |
  ```

#### Task 5.1.C — Reports & Dashboard
- `reports/arbiter_accuracy.json`, `shadow_metrics.json`, `branch_cleanup.json`, `comparison.md`, `phase4_evaluation_claim_review.csv` (hand-reviewed, like FlowContext).
- Optional tiny FastAPI dashboard: `make demo` → `uvicorn` at 0.0.0.0 exposes `/replay` + `/metrics` → preview link. Bound to 0.0.0.0, allow preview host.

#### Task 5.1.D — Documentation & Video Script
- `README.md`: 2-command quickstart, architecture diagram (Mermaid), demo GIF.
- `docs/EVALUATION.md`: methods & rubric (how arbiter accuracy measured, why gold is frozen).
- `DEMO_SCRIPT.md`: 90-sec walkthrough — "Delhi → Bangalore → but morning → don't book it → timeout → shadow".

### 5.2 Metrics (final)
All PRD-required:
- Arbiter accuracy (fixed list)
- Shadow reused / wasted
- Branch cleanup time
- Plus latency, timeout recovery, policy block

### 5.3 DoD (Submission Ready)
- [ ] `make eval` (or `uv run continuum compare`) produces all reports in <60s offline-fake
- [ ] `make baseline && make eval` reproduces published numbers within tolerance
- [ ] Docker packaging (optional, per PRD V4) if time
- [ ] 5-min demo video ≤5 min, shows: replay CLI, metrics, branch lifecycle, timeout recovery

---

## Timeline (Engineer A alone, ~10 working days)

| Day | Phase | Deliverable |
|-----|-------|-------------|
| 1 | P1 contracts + versioned state | `test_contract` green, WAL file |
| 2 | P1 provenance + stale gate + delhi demo | `replay delhi_bangalore` works hardcoded |
| 3–4 | P2 perception fast path + gold 100 | gold frozen, latency hist |
| 4–5 | P2 fused arbiter (offline-fake→ollama→gemini) | arbiter_accuracy ≥0.88 |
| 6 | P3 policy + ledger | ledger 10/10 pass |
| 7 | P3 dialogue + retraction edge cases | dont_book + after_commit + timeout scenarios |
| 8 | P4 branch manager + budget | spawn/promote/cleanup tests |
| 9 | P5 baseline + comparison | comparison.md with numbers |
| 10 | Polish demo + docs + video | submission tag `PRISM_GENAI_HACKATHON_Y2026` |

Buffer: If behind, cut P4 shadows (keep budget constants but disable spawn), keep P5 comparison as table without shadow numbers.

---

## Testing Strategy (per phase)

- `tests/test_contract.py` — schema invariants (22 tests)
- `tests/unit/test_phase1_*.py` — versioned state, provenance
- `tests/unit/test_arbiter_accuracy.py` — gold set (100 asserts)
- `tests/integration/test_replay_*.py` — full scenario replays
- `tests/safety/test_ledger_timeout.py` — injected sleeps, verify no double side effects
- `tests/perf/test_latency.py` — histogram + budget cap
- All tests offline-fake by default; `--backend gemini` gated behind env var + skipIf.

---

## Tooling

- `uv` for env, `pytest -q`, `ruff`, `mypy` (optional)
- `Makefile` targets: `sync`, `test`, `baseline`, `eval`, `ablate`, `demo`
- Git tags: `phase-1-a-foundation`, …

---

## How to Run (for judges)

```bash
git clone https://github.com/itsZaid05/new.git && cd new
# judges: main after PR #2 merges; live branch meanwhile:
git checkout arena/01a0c708-new
uv sync --extra dev          # or pip install -e .
uv run pytest -q             # 22 contract + all phase tests
uv run continuum replay data/scenarios/delhi_bangalore.json
uv run continuum eval-arbiter --gold data/gold/arbiter_100.jsonl
uv run continuum compare       # baseline vs CONTINUUM table
make demo                     # optional: FastAPI preview
```

---

## Why This Wins Prism

1. **Deterministic & reproducible** — judges can re-run in 60s, not "trust our video".
2. **Honest about tradeoffs** — reports wasted shadow cost, not hiding it.
3. **Safety as feature** — retraction-after-commit + timeout-verify are stories competitors skip.
4. **Borrowed brilliance, not NIH** — reuses RECAP, FlowContext, prism_rt, FreshCtx patterns, so code maturity is high despite short build.
5. **Latency is a number** — fast_ack histogram, not adjectives.
6. **Cut line respected** — if time runs short, V1 text-agent MVP alone satisfies PRD's 10 core properties.

