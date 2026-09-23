# CONTINUUM — Architecture for AI/ML Engineer A
## Understanding, Dialogue & Evaluation

> One-line pitch: *CONTINUUM keeps AI agents consistent when humans change their minds: it sorts what kind of change happened, keeps the work that's still valid, discards the rest, and prepares for likely next changes within strict safety and resource limits.*

---

## 1. Scope & Ownership

Engineer A owns **steps 1, 2, 3, 6(dialogue side), 9(evaluation side)** of the 9-step pipeline.

```
USER (text/audio/vision)
  │
  ▼
[1] PERCEPTION  (Fast Path, <200ms ACK)         ← A
  │
[2] INTENT DELTA EXTRACTOR (1 LLM call)         ← A  (fused with 3)
  │
[3] INTERRUPT ARBITER (5-way + confidence)      ← A  ← core IP
  │
[4] VERSIONED STATE V1→V2→V3 + merge            ← shared with B, A defines schema
  │
[5] EXECUTION / PROVENANCE GRAPH                ← B owns execution, A owns provenance schema
  │
[6] POLICY + COMMITMENT CONTROL                 ← B owns enforcement, A owns dialogue for risky
  │
[7] BRANCH MANAGER (speculation budget)         ← B owns manager, A owns shadow scoring
  │
[8] ASYNC TOOL EXECUTOR                         ← B
  │
[9] RESULT & EFFECT GATE                         ← shared, A owns stale check + ledger verify
      ▼
  RESUME / RESPOND (dialogue)                    ← A
```

**Interface contract:** All cross-team traffic goes through `continuum/contracts.py` Pydantic v2 schemas. No raw dicts.

---

## 2. Perception — FAST PATH

### Goal
Turn input (text / audio / vision) into **structured evidence with confidence** and ACK instantly, before any heavy thinking.

### Evidence Schema
```python
class EvidenceSpan(BaseModel):
    text: str                         # "Actually, Bangalore"
    modality: Literal["text","audio","vision"]
    start_ms: int | None              # for audio
    end_ms: int | None
    asr_confidence: float | None      # 0..1, None for text
    vision_bbox: tuple[int,int,int,int] | None
    transcript_confidence: float      # aggregated
    timestamp: datetime               # server receipt

class PerceptionOutput(BaseModel):
    version_in: int                   # version this turn is based on
    evidences: list[EvidenceSpan]     # usually 1 for MVP, N for multimodal
    fast_ack: str                     # "Got it — updating your destination…"
    fast_ack_latency_ms: int
    render_provenance: dict           # for UI debugging
```

### Two-Layer Fast Path
```
Layer 0 (deterministic, <5ms):
  - trim, lower, strip punct
  - backchannel lexicon: {"hmm","okay","uh","mm-hmm","yeah","…"} → NOISE fast-path
  - explicit "stop"/"cancel"/"wait"/"don't" → flag as possible RETRACT (but still call arbiter)

Layer 1 (embedding gate, <50ms, CPU):
  - MiniLM-L6-v2 embeddings of utterance vs 5 prototype centroids (precomputed)
  - cosine; if max >0.72 → confident pre-label; else mark UNCERTAIN
  - this gate ONLY decides whether to ACK as NOISE or to call LLM; never final

Layer 2 (LLM, slow path, 600-900ms):
  - only if Layer 1 uncertain OR Layer 0 flagged risk
  - single fused call (see §3)

ACK policy: Layer 0/1 always emits fast_ack within 200ms even if Layer 2 still running.
Example ACKs:
  - NOISE → (silently filtered, no ACK or "👍")
  - MODIFY → "Got it — switching to Bangalore, keeping your other constraints…"
  - RETRACT → "Understood — holding the booking…"
```

**Multimodal (stretch, Phase V3):**
- Audio: Whisper-small (~50MB INT8) via `faster-whisper` streaming; partial hypotheses 200ms chunks; aggressive endpoint detection.
- Vision: CLIP / BLIP-2 caption → text evidence; bbox → vision evidence.
- Both feed same `EvidenceSpan` list; delta extractor sees `evidence_text = " [vision: ...]  Actually, Bangalore"`

---

## 3. Intent Delta Extractor + Interrupt Arbiter — ONE MODEL CALL

### Why fused?
Baseline does 2 calls: extract delta → classify. That costs ~800ms extra and doubles $$. CONTINUUM does **one** structured-output call producing both.

### Input to LLM (few-shot, budget-matched)
```
SYSTEM: You are CONTINUUM's delta extractor + arbiter.
Given: prior committed state JSON, last assistant action (if any), new user evidence.
Output JSON with:
  delta: {op:"", field:"", old:…, new:…} | null if no semantic change
  category: NEW_GOAL | MODIFY | ADD_CONSTRAINT | RETRACT | NOISE
  confidence: float 0..1
  rationale: one sentence
  evidence_spans: [indices of EvidenceSpan used]
  suggested_clarification: string | null (only if confidence <0.7 and risky)

FEW-SHOT (5 examples, randomized order per calibration):
 1. MODIFY: "Actually, Bangalore"  (Delhi→Bangalore, keep dates)  c=0.92
 2. ADD_CONSTRAINT: "…but keep the morning constraint"  c=0.89
 3. RETRACT: "Don't book it"  c=0.94
 4. NEW_GOAL: "Forget flights, find trains"  c=0.90
 5. NOISE: "Hmm, okay…"  c=0.95

POLICY: Confidence is calibrated; be conservative on RETRACT/MUTATING.
```

### Output Schema (Pydantic, validated)
```python
class Delta(BaseModel):
    op: Literal["replace","add","remove"] | None
    field: str | None            # "destination","time_constraint","booking_instruction","goal_domain"
    old_value: Any | None
    new_value: Any | None
    span: str                    # raw phrase

class ArbiterDecision(BaseModel):
    category: Literal["NEW_GOAL","MODIFY","ADD_CONSTRAINT","RETRACT","NOISE"]
    confidence: float            # 0..1, temp-scaled
    delta: Delta | None
    rationale: str
    evidence_spans: list[int]
    suggested_clarification: str | None
    latency_ms: int
    model: str
```

### Category Semantics (normative, per PRD table)

| User says | Type | Versioned State op | Provenance op | Dialogue |
|-----------|------|--------------------|--------------|----------|
| "Actually, Bangalore" | **MODIFY** | patch `destination: Delhi→Bangalore`, invalidate `search_flights[Delhi]`, keep `dates, morning` | mark `search_BLR` as derived from V2 | "Switching to Bangalore — re-searching… (kept your morning preference)" |
| "…but keep the morning constraint" | **ADD_CONSTRAINT** | add `constraint: departure<12:00`, keep rest | no invalidation, just augment | "Added — I'll filter for mornings only." |
| "Don't book it" | **RETRACT** | prune `booking`+`payment` nodes; keep `search`+`options` | soft-delete; tombstone with cause | If booking already succeeded (IRREVERSIBLE) → "It's already booked — want me to cancel (if supported)?" |
| "Forget flights, find trains" | **NEW_GOAL** | V_new = fresh root, old graph GC'd | lineage ends, FK to previous for audit | "Starting fresh for trains — what route?" |
| "Hmm, okay…" | **NOISE** | no-op | ignored | (no state change, maybe backchannel ACK) |

### Confidence + Clarification Gate
```
if category in {RETRACT, NEW_GOAL, MUTATING} and confidence < 0.72:
    ask user: suggested_clarification
    e.g. "Do you want to cancel the booking or just change the city?"
    → wait for next EvidenceSpan, do not mutate state
else if confidence < 0.60:
    generic: "Just to confirm — you want {delta} — correct?"
else:
    apply delta to Versioned State (produce V_next)
```

**Calibration:** temperature scaling on held-out 40 examples; report ECE. Log all confidences for post-hoc.

### Latency Budget
- Fast ACK: <200ms (deterministic)
- Arbiter decision: p95 <900ms (Gemini Flash) / <400ms (offline-fake mock for CI)
- Total perception→state patch: p95 <1.1s

---

## 4. Versioned State (Shared Contract, A Defines Shape)

```python
class StateVersion(BaseModel):
    version: int                      # monotonic
    parent: int | None
    committed_evidence: list[EvidenceSpan]  # what this version is based on
    state: dict[str, Any]             # {destination:"Bangalore", dates:…, constraints:[…], goal:"flights"}
    derived_from: int                 # for audit
    created_at: datetime
    status: Literal["ACTIVE","MERGED","ABANDONED"]

class VersionedStore:
    def patch(self, base: int, delta: Delta, category: str) -> StateVersion: ...
    def merge_rapid(self, deltas: list[Delta]) -> StateVersion: ...  # coalesce <300ms burst
    def history(self) -> list[StateVersion]: ...                     # V1→V2→V3…
```

**Rapid-change merge:** If 2+ user inputs arrive within `MERGE_WINDOW_MS=300` before heavy work starts, coalesce into one `MERGED` version — avoids thrashing tool graph.

---

## 5. Provenance & Stale Gate (A's Slice)

### Provenance Vector (per step)
```python
class Provenance(BaseModel):
    step_id: str
    based_on: int                     # StateVersion
    inputs: dict[str, Any]
    axes: dict[str, float]            # freshness, capability, tool, verification ∈[0,1]
    tainted_by: dict[str, set[str]]   # which upstream steps degraded which axis
    timestamp: datetime

# Execution graph
class ExecutionNode(BaseModel):
    id: str
    kind: Literal["search","filter","book","pay","inform"]
    provenance: Provenance
    status: Literal["PENDING","RUNNING","COMPLETED","INVALIDATED","CANCELLED"]
```

**Stale check (Result & Effect Gate, step 9):**
```python
def is_stale(node: ExecutionNode, current_version: int) -> bool:
    return node.provenance.based_on < current_version and node.kind in dependents(delta)

# dependents(): MODIFY destination → invalidate search+price; ADD_CONSTRAINT → invalidate filter only; RETRACT → invalidation = prune
```

**Only affected steps invalidated; rest reused** — the demo headline number (40% faster) comes from reusing `dates` parsing + `morning` constraint.

### Effect Ledger (timeout safety)
```python
class EffectRecord(BaseModel):
    effect_id: str          # idempotency key: hash(version + tool + args)
    status: Literal["UNKNOWN","COMMITTED","ROLLED_BACK"]
    attempt: int
    last_verified_at: datetime | None
```
On timeout: do not retry blindly → `verify_after_timeout()`: query tool (did booking exist?) → reuse or safe retry with same `effect_id`.

---

## 6. Policy + Commitment Control (Dialogue Side)

| Level | Examples | Agent may | Dialogue |
|-------|----------|-----------|----------|
| **FREE** | search, read, dry-run | auto | no confirm |
| **STAGEABLE** | draft booking, hold fare | stage + ask | "I've held fare X — confirm to book?" |
| **MUTATING** | cancel search, prune graph | auto but auditable | log + brief ACK |
| **IRREVERSIBLE** | book, pay, send email | require explicit commit + effect_id | "Booking needs your go-ahead — shall I book?" |

For Engineer A: the arbiter tags risky delta (RETRACT of IRREVERSIBLE) → dialogue gate fires *before* Versioned State applies.

---

## 7. Branch Manager — Speculation Budget (A's Scoring)

**Budget (hard limits):**
- max 2 shadow branches
- max depth 3
- max tool calls / compute (configurable, e.g., 6 calls per shadow)
- READ / STAGE only (no MUTATING/IRREVERSIBLE)
- paused first when busy (PRIMARY priority)

**Engineer A role:** Score which shadows to spawn given current version + arbiter uncertainty.

```
After ArbiterDecision with confidence 0.65:
  candidates = [
    ("Bangalore morning", prob 0.55),
    ("Bangalore evening", prob 0.30),
    ("trains instead", prob 0.15)
  ]
  → spawn top-2 within budget, depth 3 search→filter→rank.

If user then says "actually morning" → promote shadow-0, discard shadow-1.
```

**Metrics (A reports):**
- reused = shadow result promoted to PRIMARY (hit)
- wasted = shadow discarded (cost)
- evaluation harness compares speculative cost vs wall-time saved

---

## 8. Dialogue & Resume

State machine:
```
Prior version V2 (Delhi) → user interrupt "Actually Bangalore" → FAST ACK → Arbiter MODIFY c=0.91 → V3 (Bangalore) → invalidated search_Bangalore running → PRIMARY resumed
```

**Retraction after action already happened (edge case):**
```
V3 had IRREVERSIBLE book_BLR succeeded @ T. User at T+5s: "don't book it"
→ Arbiter RETRACT c=0.93, but EffectRecord shows COMMITTED
→ Response: "It's already booked (Ref BLR-… at 11:03). Want me to cancel? The airline allows free cancel within 24h."
→ If user confirms → MUTATING cancel tool (needs commit)
→ Never pretend undo.
```

**Timeout edge case:**
```
book call timed out @ T+2s (status UNKNOWN)
→ ledger.verify_after_timeout(): query booking_exists(BLR, idempotency_key)
→ if exists → mark COMMITTED, reuse; else safe retry with same key.
→ dialogue: "Checking booking status…"
```

---

## 9. Evaluation — What Engineer A Must Ship

### 9.1 Arbiter Accuracy Harness
- Fixed gold set: `data/gold/arbiter_100.jsonl` — 100 hand-written utterances (20 per category), Delhi/Bangalore + 5 other domains to avoid overfit.
- Procedure: deterministic replay `continuum eval-arbiter --gold data/gold/arbiter_100.jsonl`
- Output: `reports/arbiter_accuracy.json` with macro-F1, per-category P/R/F1, confusion matrix, ECE.
- CI gate: macro-F1 ≥0.88 on `main`.

### 9.2 Latency & Budget Harness
- `data/scenarios/` — 15 scenarios (including `delhi_bangalore.json`, `dont_book_it.json`, `timeout_booking.json`, `rapid_burst.json`)
- Metrics per scenario: fast_ack_ms, arbiter_ms, version_merge_count, shadow_reused/wasted, branch_cleanup_ms, end-to-end vs baseline.

### 9.3 Shadow Metrics
- Budget enforcement test: spawn 3 shadows → assert 3rd refused/queued.
- Cleanup time: time from `CANCELLED → CLEANED_UP` (release of async tasks + memory).

### 9.4 Baseline vs CONTINUUM
- Baseline: "redo-all" agent (no versioning, no delta, 2 LLM calls, no budget).
- CONTINUUM: as spec'd.
- Report: `reports/comparison.md` table of accuracy, latency, cost, correctness on retraction-after-commit.

### 9.5 Human Evaluation (stretch)
- Pairwise plan preference (RECAP-style) on 30 rewrites: which plan better follows latest intent.
- Instructions in `docs/EVALUATION.md`.

---

## 10. Non-Goals for A (owned by B)

- Tool execution engine, async scheduling, branch GC implementation, durable WAL, SmartThings/IoT.
- But A must define the *interfaces* those components consume (provenance, version, effect_id).

---

## 11. Risks

- Over-designing multimodal before text MVP wins → mitigated by Phase order: text MVPs first, vision tagged "cut if time."
- LLM jailbreaks on RETRACT → add explicit policy prompt: "Never guess on risky actions."

---

## 12. One-Page Visual (for slides)

```
[USER] → [PERCEPTION fast ACK] → [DELTA+ARBITER (1 LLM call)] 
          → [VERSIONED STATE V1→V2→V3 merged] → [PROVENANCE GRAPH selective invalidate]
          → [POLICY GATE] → [SHADOW budget 2×3] → [ASYNC TOOLS]
          → [STALE GATE + LEDGER] → APPLY or DISCARD → [RESUME]
```

All boxes A/B labeled; A in blue, B in orange.

---

## 13. Engineer B — Plan DAG, Shadow Execution & Reuse (Implemented)

Owns steps 4(execution side)/5(graph exec)/7(manager+execution)/8/part-of-9, per
§1's ownership split. This section documents what is now genuinely wired end
to end (not just present as separate, unconnected pieces).

### 13.1 Files

| Responsibility | File | Notes |
|---|---|---|
| Plan DAG Generator | `planner.py` | `generate_plan()` — deterministic, zero-LLM, `Contract 2` `ref(step.field)` links. `step_for_hypothesis()` turns a scored hedge (`shadow.ScoredHypothesis`) into a real, executable `PlanStep`; shared by plan generation *and* the runtime so the two can't drift. `primary_search_params()` exposes the primary's intended query for genuine matching. |
| Mock Tool Sandbox / async execution | `tools.py` | Unchanged — `MockToolSandbox`, `execute_plan()` (max-concurrency DAG dispatch via `asyncio.gather`), idempotency dedup, cancellation. Reused as-is by the new runtime layer. |
| Shadow Branch Management / bounded speculation / budget | `branch_manager.py` | Unchanged — sole lifecycle + `SpeculationBudget` authority. |
| Shadow result storage | `shadow_store.py` **(new)** | `ShadowResult` / `ShadowResultStore` — an in-memory `branch_id -> ShadowResult` map (tool, normalized params, `ToolResult`, `reused` flag). `find_match()` is the genuine "does a live shadow already answer this query?" lookup. |
| Runtime integration | `orchestrator.py` **(new)** | The single integration point: dispatches shadow steps concurrently through the *existing* `execute_plan()`, re-checks `policy.risk_for()` immediately before every dispatch (belt-and-suspenders — `BranchManager`/`ShadowScorer` already filtered these), charges `BranchManager.record_call()`, and answers "which live branch matches this query?" via `promote_or_discard()`. Never mutates branch lifecycle itself — `BranchManager.promote/invalidate/cancel/cleanup` remain the only ones that do. |
| Selective reuse / stale gate | `provenance.py` | Unchanged — `invalidate_affected()`, `ProvenanceGraph.gate()`. |
| Runtime wiring | `replay.py` | Minimal, additive edit (not a rewrite): the shadow-spawn step now also builds `PlanStep`s via `step_for_hypothesis()` and dispatches them for real (`orchestrator.dispatch_shadow_branches`); the promotion step now matches the primary's *current* query (`planner.primary_search_params()`) against `shadow_store.find_match()` instead of scanning result text. All prior trace events (`shadow_spawn`, `shadow_promote`, `shadow_discard`) are preserved verbatim; two events were added (`shadow_result_ready`, `shadow_promoted_reused`). No other scenario's trace is affected — none of the other 6 gold scenarios ever spawn a shadow (their utterances sit outside `ShadowScorer`'s `[0.55, 0.72)` confidence gate). |

### 13.2 What "genuine" means here

Before this integration, a shadow branch was a `Branch` record with a score
and a label — no tool call was ever dispatched for it, and "promotion" chose
a winner by checking whether the *first word* of its label appeared in the
scripted scenario JSON's result text (a check that, for `shadow_bangalore`,
matched **both** candidates equally, since both labels start with
"Bangalore" — it degenerated to "highest score always wins").

Now, for every spawned shadow branch:

1. `planner.step_for_hypothesis()` turns its hypothesis into a real
   `PlanStep` (`search_flights` with structured `{"to", "slot"}` params).
2. `orchestrator.dispatch_shadow_branches()` runs every newly-spawned
   branch's step **concurrently**, in one `asyncio.gather` wave, through the
   real `MockToolSandbox` — a real coroutine, a real simulated network delay,
   a real `ToolResult`.
3. The result is persisted in `ShadowResultStore`, tagged with
   `branch_id`, `base_version`, `tool`, and normalized `params`.
4. When the user's next turn lands, `planner.primary_search_params(state)`
   computes what the *primary* would now search for; `store.find_match()`
   checks it against every live shadow's *actually-executed* params.
5. A match is promoted (`BranchManager.promote`) and its stored result is
   marked `reused=True` — the primary never dispatches `search_flights`
   again for that query. A non-match is invalidated → cancelled → cleaned up
   (existing `BranchManager` lifecycle, unchanged).

### 13.3 Trace shape (shadow_bangalore.json)

```
shadow_spawn        Bangalore morning   score 0.65
shadow_spawn        Bangalore evening   score 0.24
shadow_result_ready branch=...9d16bc  search_flights {to: Bangalore, slot: morning}
shadow_result_ready branch=...29deec  search_flights {to: Bangalore, slot: evening}
tool_result         search:2:1  gate=APPLY
shadow_promote      branch=...9d16bc  reused=true
shadow_promoted_reused  saved_tool_call=true  based_on_version=2  promoted_to_version=2
shadow_discard       branch=...29deec  wasted=true  cleanup_latency_ms=0.03
tool_result          search:1:0 (late Delhi, based_on=1)  gate=DISCARD
stale_discarded      search:1:0
```

### 13.4 Safety invariants preserved

- **Policy authority never moves.** `orchestrator.py` imports and calls
  `policy.risk_for()` and reads `BranchManager.budget.allowed_levels`; it
  defines no risk table of its own. A shadow step whose kind resolves to
  `MUTATING`/`IRREVERSIBLE` raises `SpeculationPolicyError` before any
  dispatch — verified by `tests/test_shadow_integration.py`.
- **Budget is unchanged.** 2 shadows / depth 3 / 6 calls per shadow — the
  orchestrator calls `BranchManager.spawn_shadow` / `record_call` exactly as
  before; it adds no second budget.
- **Idempotency & cancellation are the sandbox's, reused.** The orchestrator
  passes through `MockToolSandbox`'s existing idempotency-key dedup and lets
  `asyncio.CancelledError` propagate untouched.
- **Stale gate is `ProvenanceGraph`'s, reused.** No second staleness system
  was added; a late result from an invalidated node is still discarded by
  `ProvenanceGraph.gate()` alone.

### 13.5 Metrics now backed by real execution

`replay_scenario()`'s summary gained two fields, both derived from actual
`ShadowResultStore` state (never fabricated):

- `shadow_results`: every stored shadow result (tool, params, payload, reused flag).
- `tool_calls_saved`: count of shadow results marked `reused=True` — a real
  "we didn't have to search again" count, not an estimate.

`BranchManager.shadow_metrics()` (spawned/promoted/discarded/reused_pct/
wasted_pct/cleanup_p95_ms/primary_slowdown_pct) is unchanged and still the
source of truth for the aggregate speculation numbers in
`reports/shadow_metrics.md`.

