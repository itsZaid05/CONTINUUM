# CONTINUUM — Theme 05 Specification Tracker

> **Historical audit (24 Sep 2026).** This file records the gap analysis that
> drove the implementation; its status cells intentionally describe the older
> `cf9037c` tree. For the final 26 Sep build, use
> [`THEME05_BUILD_STATUS.md`](THEME05_BUILD_STATUS.md) and
> [`HARNESS_EDGE.md`](HARNESS_EDGE.md).

> **Purpose:** Map every requirement in the *Theme 05: Interruptible Real-Time Agents* guide (v1.0.0) to what CONTINUUM has built so far and what is still left. The work is split into two parts:
>
> - **Part A — AI/ML component** (understanding: perception, intent/delta arbitration, multimodal grounding, calibration, response quality)
> - **Part B — Agentic component** (execution: event loop, tool calls, cancellation, state, idempotency, protocol)
>
> **Audited on:** 2026-09-24 · commit `cf9037c` (`main`) · verified by reading `src/` and running `pytest` (165 passed, 1 flaky dense-model timing test excluded; Python 3.12.4)

**Legend:** ✅ Done · 🟡 Partial (exists, but not in the form the spec or harness needs) · ❌ Not started

---

## 0. Executive Summary

CONTINUUM has a strong **core for interruption semantics**: versioned state, a provenance-based stale gate, a 5-way interrupt arbiter, an idempotent effect ledger, bounded shadow speculation, and honest retraction. All of it is deterministic and tested.

The **biggest gaps are at the edges the evaluation harness will actually touch**:

1. **No harness interface.** The spec requires an agent that reads timestamped events from one async queue and writes JSON actions to another. CONTINUUM runs its own scenario format (`at_ms` / `user` / `tool_start` / `tool_result`) through `replay.py`. The protocol adapter does not exist yet.
2. **No audio or vision processing.** `Modality.AUDIO` / `VISION` exist only as enum values and schema fields. Multimodal is **50% of the test suite** (30% audio + 20% visual), and hidden multimodal scores get a **1.5× multiplier**.
3. **No tool manifests.** Tools are a hard-coded travel registry (`tools.TOOL_REGISTRY` plus `policy.risk_for`). The spec requires parsing tool definitions (read-only vs state-modifying) from per-scenario manifests, including **unseen tools**.
4. **No explicit `call_id` or cancel action.** Invalidated primary calls are handled by *discarding their late results* (stale gate), not by emitting a cancellation. The spec scores prompt cancellation within a grace period.
5. **Domain lock-in.** Arbiter, planner and slots are built for flights/hotels/cabs. The mock environment also covers **ticket creation** and **frame-grounded manual lookups**.
6. **Runtime pin.** `pyproject.toml` pins `==3.11.*` and the code imports `enum.StrEnum`, which is 3.11+ only. The spec allows **3.10–3.12**.

### Readiness at a glance (against the scoring weights)

| Scoring category | Weight | Readiness | Main blocker |
|---|---|---|---|
| Task Completion | 40% | 🟡 | Travel-domain only; no manifest-driven tool args; no snapshot in harness format |
| Interruption Recovery | 35% | 🟡 Strong core | Needs explicit `cancel` actions with `call_id` and a grace-period timer |
| Response Latency | 15% | 🟡 | Fast ACK exists (<15 ms) but is not wired to a real event stream or text chunks |
| Safety & Protocol | 10% | 🟡 | Idempotency done; JSON action schema for the harness not defined |
| Quality multiplier | 0.8–1.2× | 🟡 | Canned templates; filler-rate control and progress narration missing |
| Multimodal (1.5× hidden) | 50% of scenarios | ❌ | No ASR, no frame understanding |

---

## 1. Requirement Traceability Matrix (Spec → Code)

| # | Spec requirement (guide section) | Part | Status | Where / notes |
|---|---|---|---|---|
| 1 | Fast path: respond within a few hundred ms (§1) | A | ✅ | `perception.perceive_text`: Layer-0 fast ACK, measured p95 ≈ 1 ms |
| 2 | Slow path: async tools, multimodal processing, complex reasoning (§1) | A+B | 🟡 | Async tools ✅ (`tools.py`); multimodal ❌; LLM reasoning is env-gated and simulated when no key is set |
| 3 | Coordination layer: non-blocking execution, cancellation, snapshot updates, idempotency (§1) | B | 🟡 | Branch manager, ledger and versioned store ✅; primary-call cancellation and snapshot emission ❌ |
| 4 | Two async queues: events in, actions out (§3) | B | ❌ | No `asyncio.Queue`-based agent loop |
| 5 | Input: transcribed text chunks + end-of-turn markers (§3.1) | A | ❌ | Perception takes full utterances only |
| 6 | Input: raw audio clips (WAV) (§3.1) | A | ❌ | Not implemented |
| 7 | Input: video frames (PNG) (§3.1) | A | ❌ | Not implemented |
| 8 | Input: interruption signals (§3.1) | B | 🟡 | Interruptions are inferred from user turns; no handler for an explicit `interrupt` event |
| 9 | Input: async tool results (§3.1) | B | 🟡 | Handled in `replay.py` in the scenario format, not the harness format |
| 10 | Input: scenario tool manifests (§3.1) | B | ❌ | Hard-coded `TOOL_REGISTRY` |
| 11 | Output: spoken fillers (§3.1) | A | 🟡 | `fast_ack` templates; no filler budget or progress narration |
| 12 | Output: non-blocking tool calls with explicit `call_id` (§3.1) | B | 🟡 | Non-blocking ✅; uses `step_id` / `node_id` / `idempotency_key`, no `call_id` |
| 13 | Output: cancellations (§3.1) | B | 🟡 | Shadow branches only (`BranchManager.cancel`); primary calls are stale-discarded, not cancelled |
| 14 | Output: clarification requests (§3.1) | A | ✅ | `delta_arbiter._clarification_for`, `policy.low_confidence_question`, `dialogue` `clarify` |
| 15 | Output: final response + State Snapshot (intent + slots) (§3.1) | A+B | 🟡 | `StateVersion.state` holds slots; not emitted as a snapshot in the final response |
| 16 | Floor management: no false completion claims, no excess fillers (§3.2.1) | A | 🟡 | Honest retraction ✅; no "claim-only-after-confirmed-result" check; no filler limiter |
| 17 | Interruption recovery: cancel within grace period, update snapshot, re-plan (§3.2.2) | B | 🟡 | Re-plan ✅ (`planner.affected_steps`), invalidation ✅; cancel timing ❌ |
| 18 | Session slot tracking + localized corrections (§3.2.3) | A+B | ✅/🟡 | `VersionedStore.patch` applies field-level deltas; slot vocabulary is travel-only |
| 19 | Schema-driven tools, read-only vs state-modifying, no duplicates (§3.2.4) | B | 🟡 | Risk tiers + ledger idempotency ✅; manifest parsing ❌ |
| 20 | Multimodal grounding + clarify ambiguous perception (§3.2.5) | A | ❌ | — |
| 21 | Well-formed JSON payloads, valid snapshots and ids (§3.2.6) | B | 🟡 | Pydantic v2 frozen contracts ✅; harness action schema ❌ |
| 22 | Retries / fault injection handling (§4) | B | ✅/🟡 | `ledger.verify_after_timeout` (10-case matrix); no generic retry-with-backoff for read-only tools |
| 23 | Chained calls (§4) | B | ✅ | `tools.execute_plan` resolves `ref(step.field)` dependencies |
| 24 | Unseen tools (§4) | B | ❌ | `spec_for()` raises `KeyError` for unknown tools |
| 25 | Python 3.10–3.12 (§6) | B | 🟡 | Runs on 3.12; `requires-python==3.11.*` and `StrEnum` break 3.10 |
| 26 | 120 s per scenario cap; 300 s warm-up hook (§6) | B | ❌ | No warm-up hook (dense model / ASR preload); no watchdog |
| 27 | Session-scoped memory only (§6) | B | 🟡 | In-memory by default; optional WAL file and shadow "history boost" must be reset per session |

---

## PART A — AI/ML Component

*Scope: turning raw user signals (text, audio, frames) into structured, calibrated understanding, and turning agent state back into natural, truthful speech.*

### A.1 Completed ✅

| Capability | Implementation | Evidence |
|---|---|---|
| **Perception fast path (Layer 0)**: backchannel lexicon, retract/modify markers, instant ACK | `src/continuum/perception.py` | `tests/test_perception.py`; fast ACK p95 ≈ 1 ms (<200 ms target) |
| **Fused delta + interrupt arbiter**: one structured call returns `{delta, category, confidence, rationale, spans, clarification}` | `src/continuum/delta_arbiter.py` | `tests/test_arbiter.py`; 100% / macro-F1 1.0 on gold set |
| **5-way interrupt taxonomy**: NEW_GOAL / MODIFY / ADD_CONSTRAINT / RETRACT / NOISE | `contracts.ArbiterCategory` | Normative table in `docs/ARCHITECTURE_A.md §3` |
| **LLM adapters**: Ollama (qwen3:4b), Gemini, OpenAI; JSON parsing with validation fallback | `src/continuum/llm.py` | `tests/test_llm_adapter.py` (24 tests) |
| **Dense embedding gate**: MiniLM centroids, `local_files_only` | `llm.dense_classify` | `reports/arbiter_dense.md` |
| **Confidence calibration**: temperature scaling T=1.2, conservative on RETRACT / NEW_GOAL; 10-bin ECE reported (0.106) | `llm.calibrate_confidence` | `reports/arbiter_accuracy.md` |
| **Clarify-on-low-confidence**: risky categories below 0.72 trigger a question instead of a state change | `delta_arbiter._clarification_for`, `policy.low_confidence_question` | `tests/test_policy.py`, `tests/test_dialogue.py` |
| **Dialogue manager**: 10 templates; honest "already booked, want me to cancel?" after commit; timeout "checking status" | `src/continuum/dialogue.py` | `retract_after_commit` scenario |
| **Shadow hypothesis scorer**: top-2 alternative interpretations for ambiguous turns (confidence 0.55–0.72) | `src/continuum/shadow.py` | `tests/test_shadow.py`; `reports/shadow_scores.jsonl` |
| **Gold evaluation set**: 100 utterances, 20 per category, frozen hash `b8920267657a` | `data/gold/arbiter_100.jsonl`, `scripts/make_gold.py` | `docs/EVALUATION.md` |
| **Evaluation harness (arbiter)**: accuracy, per-class P/R/F1, confusion matrix, latency p50/p95, ECE | `cli.eval_arbiter` | `reports/arbiter_*.{json,md}` |
| **Schema support for multimodal evidence**: `EvidenceSpan.modality`, `asr_confidence`, `vision_bbox`, `start_ms/end_ms` | `contracts.EvidenceSpan` | `tests/test_contract.py` |

### A.2 Partial 🟡

| Capability | What exists | What's missing |
|---|---|---|
| **Real-model accuracy** | Adapters work; numbers for Gemini / Ollama / OpenAI are **simulated latency offsets on the offline table** when no key or host is set | Run a real eval with keys; report real accuracy and latency. The gold set was built to be pattern-coverable by `offline-fake`, so 100% on it is **not** evidence of generalisation |
| **Slot extraction** | Travel fields: `destination`, `time_constraint`, `booking_instruction`, `goal_domain` | Generic, manifest-driven slot extraction (e.g. ticket `priority` / `category`, device `model` / `error_code`) |
| **Fillers and floor management** | One fast ACK per turn | Filler budget (no repeats inside N ms); progress narration during long tools ("still searching…"); guard that forbids "done / booked" until a confirmed tool result exists |
| **Self-repair handling** (accessibility use case) | "Actually X" / "no, Y" markers | Disfluency handling within one turn ("to Del— uh, Bangalore"), restarts, filled pauses spread across text chunks |
| **Response grounding** | Templates use state values (`{new}`, `{ref}`) | Final response must cite actual tool-result fields (flight number, ticket id, manual step); needed for the quality multiplier |

### A.3 Remaining ❌

| # | Item | Why it matters (spec) | Suggested approach |
|---|---|---|---|
| A-R1 | **Streaming text-chunk perception** with end-of-turn (EOT) markers | §3.1 inputs; latency score counts from user input | Incremental buffer per turn; run Layer-0 on every chunk (early ACK, early interruption detection); run the arbiter on EOT, or earlier when a strong marker appears ("actually", "cancel") |
| A-R2 | **Audio (WAV) → text** | 30% of scenarios; 1.5× hidden multiplier | `faster-whisper` (small / base INT8) preloaded in the 300 s warm-up; emit `EvidenceSpan(modality=audio, asr_confidence=avg_logprob→prob)`; ACK immediately while ASR runs on the slow path |
| A-R3 | **Low-ASR-confidence clarification** | §3.2.5 "clarify ambiguous perceptions" | If `asr_confidence < τ` on a slot-bearing token (city, date, id), ask "Did you say Bangalore or Mangalore?" instead of mutating state |
| A-R4 | **Video frame (PNG) grounding** | 20% of scenarios; "frame-grounded manual lookups" | Small VLM or OCR + captioning (e.g. Florence-2 / BLIP / PaddleOCR / Tesseract) → text evidence (device model, error code, LED state) → slot fill → `manual_lookup` tool args |
| A-R5 | **Ambiguous-frame clarification** | §3.2.5 | Low detection / OCR confidence or several candidate objects → "I see two devices; which one?" |
| A-R6 | **Cross-modal fusion** | "Grounding device queries in camera frames" | Merge `[vision: …]` and `[audio: …]` evidence into the fused arbiter prompt (already sketched in `ARCHITECTURE_A.md §2`) |
| A-R7 | **Domain-general arbiter** | Hidden set: ~60 scenarios incl. support bots, troubleshooting, unseen tools | Give the LLM or dense gate the tool manifest and current slots as context; build a second gold set outside travel (tickets, troubleshooting, in-car) |
| A-R8 | **Gold set v2 (held-out, adversarial)** | Honest generalisation numbers | ~150 utterances not written against the regex table; include hesitations, compound turns ("change to Pune and make it evening"), ASR-noisy text |
| A-R9 | **Final-response generator with state snapshot** | 40% Task Completion includes "proper final response grounding" | Template or LLM that renders from `{intent, slots, tool_results}`; truthfulness check: every entity mentioned must appear in a tool result or in slots |
| A-R10 | **Filler / narration policy** | Floor management; quality multiplier | Rules: ACK ≤ 300 ms after EOT; progress line only when a tool runs > ~1.5 s; max 1 filler per tool; never "done" before a result |

### A.4 AI/ML Definition of Done

- [ ] Real-LLM arbiter accuracy reported on gold v1 **and** held-out gold v2 (non-travel included)
- [ ] ASR pipeline: WAV → EvidenceSpan with confidence; clarifies low-confidence slots
- [ ] Frame pipeline: PNG → grounded slots (model / error code); clarifies ambiguous frames
- [ ] Chunked text perception with EOT; time-to-first-ACK measured from the event timestamp
- [ ] Final responses mention only grounded entities (automated truthfulness check in tests)
- [ ] Filler policy unit-tested (no duplicates, no false completion claims)

---

## PART B — Agentic Component

*Scope: the real-time coordination layer — event loop, tool dispatch and cancellation, state consistency, idempotency, and protocol compliance.*

### B.1 Completed ✅

| Capability | Implementation | Evidence |
|---|---|---|
| **Frozen contracts** (Pydantic v2) for all pipeline stages | `src/continuum/contracts.py` | `tests/test_contract.py` (22) |
| **Versioned state**: V1→V2→V3 chain, parent links, WAL option, `merge_rapid` (300 ms burst coalescing) | `src/continuum/versioned_state.py` | `tests/test_versioned_state.py` |
| **Provenance graph + stale gate**: every node records `based_on` version; selective invalidation by category/field; late results → `DISCARD` | `src/continuum/provenance.py` | `tests/test_provenance.py`; ablation shows 2 stale results leak without the gate |
| **Risk tiers**: FREE / STAGEABLE / MUTATING / IRREVERSIBLE (one authoritative table) | `policy.risk_for` | `tests/test_policy.py` |
| **CommitGate**: ALLOW / STAGE / CONFIRM_REQUIRED / BLOCK (IRREVERSIBLE needs confirmation; BLOCK under RETRACT) | `policy.CommitGate` | `tests/test_policy.py` |
| **Effect ledger / idempotency**: `sha256(version+tool+args)` keys; COMMITTED effects are never re-dispatched; `verify_after_timeout` instead of blind retry | `src/continuum/ledger.py` | 10-case timeout matrix, zero double-books |
| **Duplicate result delivery guard**: second delivery of the same result is ignored | `replay.py` | `duplicate_result` scenario + test |
| **Async mock tool sandbox**: real `async def` tools with `asyncio.sleep` delays, so `task.cancel()` actually interrupts; `external_operation_id` minted on accept | `src/continuum/tools.py` | `tests/test_tools.py` |
| **Plan DAG generator**: deterministic (category, field) → search → hold → confirm, with `ref()` links; `affected_steps(old, new)` for re-planning | `src/continuum/planner.py` | `tests/test_planner.py` |
| **Chained execution**: `execute_plan` resolves dependencies and runs independent steps with `asyncio.gather` | `tools.execute_plan` | `tests/test_tools.py` |
| **Branch manager / speculation budget**: max 2 shadows, depth ≤ 3, ≤ 6 calls, FREE/STAGEABLE only, pause when busy; full lifecycle incl. ABANDONED | `src/continuum/branch_manager.py` | `tests/test_branch*.py`; cleanup p95 ≈ 0.009 ms |
| **Shadow orchestrator + result store**: really dispatches shadows, matches by normalised query, promotes and reuses (saves a tool call) or cancels and cleans up | `orchestrator.py`, `shadow_store.py` | `tests/test_shadow_integration.py` (11); primary slowdown 4.5% (cap 5%) |
| **Deterministic replay** on a stepped virtual clock (`at_ms`) with a full event trace | `src/continuum/replay.py` | 7 scenarios, all green |
| **Naive baseline + comparison + ablation** | `baseline.py`, `cli compare / ablate` | `reports/comparison.md`, `reports/ablation.md` |
| **CLI + FastAPI preview** | `cli.py`, `api.py` | `make eval`, `make demo` |

### B.2 Partial 🟡

| Capability | What exists | What's missing |
|---|---|---|
| **Cancellation of superseded calls** | Shadow losers go through `BranchManager.cancel` → CLEANED_UP; `dispatch_shadow_branch_task` gives cancellable tasks | Primary in-flight calls (e.g. `search:Delhi` after "Actually, Bangalore") are **not cancelled**; their result is dropped by the stale gate when it arrives. The spec scores an explicit `cancel(call_id)` within a few-ms grace period |
| **Tool-call identity** | `step_id`, `node_id`, `idempotency_key`, `external_operation_id` | A `call_id` on every emitted tool call, echoed by results and used by cancellations |
| **State snapshot** | `StateVersion` (immutable, versioned) | Emit `{intent, slots}` in the harness schema on every final response, and after every interruption |
| **Interruption signal** | Arbiter infers interruption from the text | Handle an explicit `interrupt` event: stop current speech, freeze or cancel affected calls, then re-arbitrate |
| **Retry policy** | Mutating calls: verify after timeout, no blind retry | Read-only calls: bounded retry with backoff on injected faults; surface failure honestly after N attempts |
| **Burst coalescing in live flow** | `merge_rapid` is unit-tested on the store | Replay still shows 3 separate patches for `rapid_burst`; the live loop should debounce before dispatching heavy work |
| **Python compatibility** | Runs on 3.11 / 3.12 | Replace `StrEnum` with a `(str, Enum)` shim; widen `requires-python` to `>=3.10,<3.13`; run CI on 3.10 |
| **Session isolation** | In-memory stores by default | Guarantee a fresh store, ledger, branch manager and scorer history per scenario; no WAL / HF cache writes that carry state across sessions |

### B.3 Remaining ❌

| # | Item | Why it matters (spec) | Suggested approach |
|---|---|---|---|
| B-R1 | **Harness adapter: two async queues** | §3 interface contract; without it no score is possible | `async def run(events: asyncio.Queue, actions: asyncio.Queue)`; one consumer task per event type; fast-path and slow-path tasks never block the reader |
| B-R2 | **Event schema ingestion** | §3.1 inputs | Parse: `text_chunk` (+ `eot`), `audio` (WAV path/bytes), `frame` (PNG), `interrupt`, `tool_result` (by `call_id`), `manifest`. Map to existing `EvidenceSpan` / `ExecutionNode`. **Finalise once the evaluation kit is released** |
| B-R3 | **Action schema emission** | §3.1 outputs, §3.2.6 protocol | Pydantic models for `speak` (filler / final), `tool_call{call_id, tool, args}`, `cancel{call_id}`, `clarify`, `final{text, snapshot{intent, slots}}`; validate before `put()`; JSON-schema test for every action |
| B-R4 | **Manifest-driven tool registry** | §3.2.4; "unseen tools" in public and hidden sets | Build `ToolSpec` at runtime from the manifest (name, arg schema, `read_only` / `state_modifying`); map to risk tiers (read-only → FREE, state-modifying → MUTATING, or IRREVERSIBLE when flagged); validate args against the JSON schema before dispatch; remove the `KeyError` path |
| B-R5 | **Generic planner** | Unseen tools; support / troubleshooting domains | Choose tools from the manifest based on intent + filled slots (LLM or rule-based matching of required args); keep the deterministic travel templates as a fast path |
| B-R6 | **Prompt cancellation with grace-period timer** | 35% Interruption Recovery | On invalidation: `task.cancel()` + emit `cancel{call_id}` in the same event-loop tick; record `cancel_latency_ms`; test that it stays under the grace period |
| B-R7 | **No stale reruns** | "absence of stale re-runs" | After cancel, block re-dispatch of the same (tool, args) under an older version; reuse a still-valid in-flight call if the args did not change |
| B-R8 | **Duplicate-state-change guard at the protocol level** | 10% Safety: *zero* duplicate state-changing calls | Ledger check on every outgoing `tool_call` where the manifest says state-modifying, keyed on (tool, normalised args, session); covers retries, interruptions and shadow promotion |
| B-R9 | **Virtual-clock compatibility** | Harness replays on a virtual clock | Avoid wall-clock `time.perf_counter` / `asyncio.sleep` in decision logic; take timestamps from events; make latency measurement clock-agnostic |
| B-R10 | **Warm-up hook + watchdog** | §6: 300 s setup, 120 s per scenario | `setup()` preloads MiniLM, Whisper and the vision model; per-scenario watchdog emits a safe final response before 120 s |
| B-R11 | **End-to-end harness tests** | Nine public scenarios | Port the 9 public scenarios when released; add regression tests asserting trace-level metrics (cancel latency, duplicate count, snapshot accuracy) |
| B-R12 | **Packaging** | Submission | Docker image or `uv` lockfile for 3.10–3.12; one command to run the agent against the kit |

### B.4 Agentic Definition of Done

- [ ] Agent passes the 9 public scenarios through the official harness queues
- [ ] Every tool call has a `call_id`; every superseded call gets a `cancel` inside the grace period
- [ ] Zero duplicate state-changing calls across all scenarios (automated assertion)
- [ ] Unseen tools from a manifest can be called with schema-valid args
- [ ] Every final response carries a valid `{intent, slots}` snapshot
- [ ] Runs on Python 3.10, 3.11 and 3.12; warm-up < 300 s; every scenario < 120 s

---

## 2. Use-Case Coverage (Guide §2)

| Use case | Status | Notes |
|---|---|---|
| **In-car: drop stale route calculations when destination changes** | ✅ (flight analogue) | `delhi_bangalore`: stale Delhi result discarded, morning constraint kept. Needs a route / navigation tool via manifest and real cancel actions |
| **Customer support: adjust parameters mid-booking without double-booking** | ✅ | `timeout_booking`, `dont_book_it`, `retract_after_commit`, ledger idempotency |
| **Field troubleshooting: ground queries in camera frames and manuals** | ❌ | Needs A-R4/A-R5 (vision) and B-R4/B-R5 (manual-lookup tool via manifest) |
| **Accessibility: speech hesitations and self-repairs** | 🟡 | NOISE / backchannel filtering and "actually…" repairs work; in-utterance disfluency and ASR input missing (A-R1, A-R2) |

## 3. Test-Suite Coverage (Guide §4)

| Public-suite theme | Covered by an existing scenario? |
|---|---|
| Interruptions | ✅ `delhi_bangalore`, `rapid_burst`, `shadow_bangalore` |
| Chained calls | ✅ planner `ref()` chains (unit-tested); no dedicated scenario |
| Retries | 🟡 `timeout_booking` (mutating verify); no read-only retry scenario |
| Clarifications | 🟡 Unit-tested; no end-to-end clarification scenario |
| Unseen tools | ❌ |
| Audio scenarios (30%) | ❌ |
| Visual scenarios (20%) | ❌ |

---

## 4. Suggested Priority Order

Ordered by score impact per unit of effort:

| Priority | Item | Owner | Reason |
|---|---|---|---|
| **P0** | B-R1, B-R2, B-R3: harness queues + event/action schemas | Agentic | Nothing scores without them. Stub now, finalise when the kit is released |
| **P0** | B-R6, B-R7: explicit `call_id` cancellation + no stale reruns | Agentic | 35% weight; the core logic already exists, it just isn't emitted |
| **P0** | Python 3.10 compatibility (`StrEnum` shim, widen pin) | Agentic | Cheap; avoids a hard failure if the runner uses 3.10 |
| **P1** | B-R4, B-R5: manifest-driven tools + generic planner | Agentic | Unseen tools are in both public and hidden sets |
| **P1** | A-R2, A-R3: ASR + low-confidence clarification | AI/ML | 30% of scenarios, 1.5× hidden multiplier |
| **P1** | A-R9, A-R10 + snapshot emission: grounded final response, filler policy | AI/ML | 40% Task Completion + quality multiplier |
| **P2** | A-R4, A-R5, A-R6: frame grounding | AI/ML | 20% of scenarios, 1.5× multiplier; heavier models |
| **P2** | A-R1: chunked perception with EOT | AI/ML | Latency (15%) and early interruption detection |
| **P2** | A-R7, A-R8: domain-general arbiter + held-out gold v2 | AI/ML | Hidden-set robustness; honest metrics |
| **P3** | B-R9 – B-R12: virtual-clock hygiene, warm-up / watchdog, e2e tests, packaging | Agentic | Submission hardening |

---

## 5. Known Documentation Inconsistencies

- `README.md` badges and quickstart say **137** tests; `docs/STATUS.md` says **165**; the current run is **165 passed + 1 flaky** (dense cold-load timing, `test_llm_adapter.py::test_dense_local_files_only_no_download`).
- `README.md` says Python **3.11**; the spec requires 3.10–3.12.
- `README.md` quickstart points to `itsZaid05/new` and branch `arena/01a0c708-new`; the work is now on `main`.
- `perception.py` docstring still says "Phase 1: … Embedding gate + ASR are Phase 2". The embedding gate lives in `llm.py`; ASR is not built.
- `ledger.py` docstring says "Phase 3 will add tool-client verify hook", but `verify_after_timeout(exists_fn=…)` already exists.
