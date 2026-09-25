# CONTINUUM — Theme 05 Build Status: What's Built, What's Left

> **Sources reconciled:** *Theme 05: Interruptible Real-Time Agents* guide (v1.0.0) and *CONTINUUM Updated Theme 05 Specification & Implementation Plan* (24 Sep 2026).
> **Audited against code:** `main` @ `b227b25` (25 Sep 2026). This includes the two new commits that added the harness runtime, tool manifests, WAV/PNG ingestion and the Python 3.10 shim.
> **Test run:** `pytest` → **171 passed** on Python 3.12.4 (one flaky dense-model timing test excluded).
> **Method:** read every module in `src/continuum/`, ran the suite, and drove `AgentRuntime` directly with a probe script (results in §4).
> **Supersedes:** `docs/THEME05_SPEC_TRACKER.md` (written before `a874d78` / `b227b25`).

**Legend:** ✅ Built and tested · 🟡 Partly built (exists but incomplete, or not connected to the rest of the system) · ❌ Not built

> **Update 25 Sep 2026 — AI/ML B delivered** (details and results in [`AIML_B.md`](AIML_B.md)). Defects **D1 (args), D2, D3, D4, D5, D6, D7 are fixed**. Items **B-2** (external tool mode), **B-4** (errors → clarification, crash-guarded loop), **B-7** (generic planner), **B-8** (retry / verify), **B-9** (idempotency across re-plans), **B-10** (manifest hardening), **B-11** (session isolation), **B-12** (shadows on the runtime path, opt-in) and **B-13** (cancel metrics) are done. **A-1** (slot binding) is done on the planner side. **B-3** is partial: actions carry `ts_ms` and events accept `ts_ms`, but the harness clock is not yet authoritative. Evaluation: planner held-out **96.4%** fully correct turns (0 unsafe writes); runtime suite 100 vs 54.7 for the pre-planner runtime, with four ablations. Still open from the P0 list: **B-1** (provenance/ledger on the runtime path — plan-diff identity is used instead), **B-5** (`datetime.UTC` still blocks Python 3.10), **B-6** (map to the official kit schema), and **A-2** (fillers).

---

## 1. Where We Stand

The updated specification sums it up: *"the project is not missing another big idea. It is missing several interfaces and edge capabilities that the official harness can directly exercise."*

As of `b227b25`, those interfaces **now exist as a first draft**:

- `AgentRuntime` in `src/continuum/runtime.py`: two async queues, typed events and actions, `call_id`, and explicit `cancel`
- `ToolManifest` / `ToolRegistry` in `tools.py`, for tools that are only described at runtime
- `perceive_audio` / `perceive_frame` in `perception.py`, for WAV and PNG input
- `_compat.StrEnum` and a widened `requires-python` for Python 3.10

**The central problem now is that the new runtime is a thin shell that bypasses the execution kernel.** `AgentRuntime` calls the arbiter and the versioned store directly. It does **not** use:

- the provenance graph (selective invalidation)
- the planner DAG
- the CommitGate or policy engine
- the effect ledger
- the branch manager or shadows
- the dialogue manager

The things CONTINUUM is strongest at are therefore **not on the path the harness will score**. Joining the two is the single most important piece of remaining work.

### Readiness against the scoring framework

| Scoring category | Weight | Kernel (replay path) | Harness path (`AgentRuntime`) | Main gap |
|---|---|---|---|---|
| Task Completion | 40% | 🟡 travel-only | ❌ | Tool args come out empty; only read-only tools are ever called; snapshot `intent` is just a copy of `slots` |
| Interruption Recovery | 35% | ✅ strong | 🟡 | Cancels **every** in-flight call on any change (no selective reuse); tool results sent in by the harness are ignored |
| Response Latency | 15% | ✅ fast ACK ~1 ms | 🟡 | ACK is immediate, but events carry no timestamp, so latency can't be measured against the virtual clock |
| Safety & Protocol | 10% | ✅ ledger + CommitGate | 🟡 | Actions are schema-valid Pydantic models, but no CommitGate or ledger sits on the runtime path; `idempotency_key = call_id`, so re-planned duplicates aren't caught |
| Quality multiplier (0.8–1.2×) | — | 🟡 templates | ❌ | 2–3 "Got it…" fillers per turn; final text is "`<tool>` completed." |
| Multimodal (50% of scenarios, 1.5× hidden) | — | — | 🟡 intake only | No ASR and no OCR/VLM: real WAV/PNG input always leads to a clarification request |

---

## 2. Requirement Matrix (Guide + Updated Spec → Code)

| # | Requirement | Source | Status | Evidence / gap |
|---|---|---|---|---|
| 1 | Fast path: immediate ACK, never blocked by slow inference | Guide §1, Spec §3 | ✅ | `perception.perceive_text` (p95 ≈ 1 ms); runtime emits `speak` before arbitration |
| 2 | Slow path: async tools + reasoning + multimodal | Guide §1 | 🟡 | Async tools ✅; LLM arbiter behind env keys; multimodal inference ❌ |
| 3 | Coordination layer: non-blocking, cancellation, snapshots, idempotency | Guide §1 | 🟡 | All exist in the kernel; not joined up in `AgentRuntime` |
| 4 | Two async queues (events in, actions out) | Guide §3, Spec B-R1 | ✅ skeleton | `AgentRuntime.input_queue` / `output_queue`, `serve()` loop |
| 5 | Timestamped events | Guide §3 | ❌ | `RuntimeEvent` has no `timestamp` / `at_ms`; actions have none either |
| 6 | Text chunks + end-of-turn | Guide §3.1, A-R1 | 🟡 | Chunks are buffered, arbiter runs on EOT ✅; no early marker detection; a filler is sent **per chunk** |
| 7 | WAV audio + ASR confidence | Guide §3.1, A-R2 | 🟡 intake only | Validates the WAV; reads a transcript only from RIFF `ICMT`/`INAM` metadata; confidence hard-coded to 0.85; no ASR engine |
| 8 | PNG frames + grounded evidence | Guide §3.1, A-R4 | 🟡 intake only | Validates the PNG; reads only `tEXt` chunks; no OCR/VLM |
| 9 | Interruption signal | Guide §3.1 | ✅ | `EventType.INTERRUPT` cancels one `call_id`, or all calls when none is given |
| 10 | Async tool results from the harness | Guide §3.1 | ❌ | `TOOL_RESULT` is validated and then **dropped**; the runtime runs tools in its own `MockToolSandbox` |
| 11 | Scenario tool manifests | Guide §3.1, B-R4 | 🟡 | `MANIFEST` event → `registry.register` ✅; the registry is shared across sessions |
| 12 | Spoken fillers without excess | Guide §3.2.1, A-R10 | 🟡 | Fillers emitted, but 2–3 per turn; no budget or progress narration |
| 13 | Tool calls with explicit `call_id` | Guide §3.1 | ✅ | `ToolCallAction{call_id, tool, args}` (uuid4) |
| 14 | Cancellation within the grace period | Guide §3.2.2, B-R6 | ✅/🟡 | `task.cancel()` + `CancelAction` + `cancel_grace_s=0.05` ✅; cancel latency not measured or logged |
| 15 | No stale reruns | B-R7 | 🟡 | Late results are rejected by the `cancelled` flag and version check ✅; the same args are re-dispatched under a new version even when still valid |
| 16 | Selective invalidation (keep valid work) | Spec §1, §6 | 🟡 | Kernel ✅ (`provenance.invalidate_affected`, now lineage-aware via `input_fields` / `dependencies`); runtime cancels **all** calls |
| 17 | Clarification requests | Guide §3.1 | ✅ | `ClarifyAction` for low confidence, unreadable audio, ungrounded frames |
| 18 | Final response + snapshot `{intent, slots}` | Guide §3.1, Spec §8 | 🟡 | `FinalAction.snapshot` exists; `intent` is the same dict as `slots`; the raw tool payload is placed into the slots |
| 19 | Session slot tracking + localized corrections | Guide §3.2.3 | 🟡 | `VersionedStore.patch` ✅; slots never reach the tool args in the runtime (probe shows `args: {}`) |
| 20 | Schema-driven tools, read-only vs state-modifying | Guide §3.2.4 | 🟡 | `ToolManifest.mutation_class`, `bind_args` checks required args ✅; `mutation_class` is a free string; `choose_read_tool` picks the **first** read-only tool whatever the intent |
| 21 | Zero duplicate state-changing calls | Guide §3.2.4, §5 | 🟡 | Kernel ledger ✅; runtime never calls state-changing tools, and when it does, `call_id` as the idempotency key won't dedupe |
| 22 | Unseen tools | Guide §4 | 🟡 | Registration + arg binding ✅; a missing required arg raises `ValueError` out of `handle()` (crashes `serve()`) |
| 23 | Retries / fault injection | Guide §4 | 🟡 | Ledger `verify_after_timeout` ✅; runtime turns any tool exception into a clarify message with the exception text; no read-only retry |
| 24 | Chained calls | Guide §4 | 🟡 | `execute_plan` + `ref()` ✅ in the kernel; runtime issues one call per turn |
| 25 | Multimodal clarification of ambiguous perception | Guide §3.2.5 | ✅ (by default) | Everything multimodal is ambiguous, because there is no recognizer |
| 26 | Python 3.10–3.12 | Guide §6 | 🟡 | `StrEnum` shim ✅, `requires-python >=3.10,<3.13` ✅; **`from datetime import UTC` (3.11+) remains** in `contracts.py` and `branch_manager.py`, so import still fails on 3.10 |
| 27 | 300 s warm-up, 120 s per-scenario cap | Guide §6 | ❌ | No `setup()` hook, no watchdog |
| 28 | Session-scoped memory only | Guide §6 | 🟡 | Per-session store, calls and text ✅; registry, sandbox dedup and the shadow-scorer history are process-wide |
| 29 | IVS in arbiter + policy | Spec §3, §8 | ❌ | No IVS anywhere in the code; the acronym isn't defined in the repo — needs a definition first |
| 30 | Authorization for irreversible actions | Spec §3 | 🟡 | CommitGate `CONFIRM_REQUIRED` ✅; `ToolManifest.authorization` is declared but never read |
| 31 | `/api/v1/intent/classify`, `/plan/generate`, `/effect/verify` | Spec §8 | ❌ | `api.py` has only GET preview routes |
| 32 | Docker / one-command clean-clone run | Spec §12 | 🟡 | `Dockerfile` exists (3.12 only); its `CMD` runs `pytest`, not the agent |

---

## 3. What Has Been Built

### PART A — AI/ML (understanding, perception, dialogue, evaluation)

| Capability | Module | Tests / evidence |
|---|---|---|
| Fast-path perception, Layer 0: backchannel lexicon, retract/modify markers, ACK templates | `perception.py` | `test_perception.py`; ACK p95 ≈ 1 ms |
| **New:** WAV intake: validates PCM, uses an upstream transcript if embedded, otherwise marks the input ambiguous (never acts on unheard audio) | `perception.perceive_audio` | `test_perception_multimodal.py` |
| **New:** PNG intake: validates the signature, uses embedded `tEXt` as OCR evidence, otherwise marks it ambiguous | `perception.perceive_frame` | `test_perception_multimodal.py` |
| Fused delta + 5-way arbiter (NEW_GOAL / MODIFY / ADD_CONSTRAINT / RETRACT / NOISE), one call | `delta_arbiter.py` | `test_arbiter.py`; 100% on gold v1 (see caveat in A-5) |
| LLM adapters: Ollama, Gemini, OpenAI; structured JSON with validation fallback | `llm.py` | `test_llm_adapter.py` (24) |
| Dense MiniLM centroid gate | `llm.dense_classify` | `reports/arbiter_dense.md` |
| Confidence calibration (T=1.2), 10-bin ECE (0.106) | `llm.calibrate_confidence` | `reports/arbiter_accuracy.md` |
| Clarify instead of mutating on low-confidence risky turns | `delta_arbiter`, `policy.low_confidence_question` | `test_policy.py`, `test_dialogue.py` |
| Dialogue manager: honest retraction after commit, timeout "checking status", 10 templates | `dialogue.py` | `retract_after_commit` scenario |
| Shadow hypothesis scorer: top-2 alternative readings in the 0.55–0.72 confidence band | `shadow.py` | `test_shadow.py`, `reports/shadow_scores.jsonl` |
| Gold set v1: 100 utterances, frozen hash `b8920267657a`; eval CLI with P/R/F1, confusion matrix, latency | `data/gold/`, `cli eval-arbiter` | `reports/arbiter_*.md` |

### PART B — Agentic (coordination, execution, safety, protocol)

| Capability | Module | Tests / evidence |
|---|---|---|
| **New:** Two-queue async runtime; per-session store, calls and text buffer | `runtime.AgentRuntime` | `test_runtime_contract.py` |
| **New:** Typed event model (`text`, `eot`, `audio`, `frame`, `interrupt`, `tool_result`, `manifest`) | `runtime.RuntimeEvent` | ″ |
| **New:** Discriminated action union: `speak`, `tool_call{call_id}`, `cancel{call_id}`, `clarify`, `final{text, snapshot}` | `runtime.RuntimeAction` | ″ |
| **New:** Explicit cancellation: `task.cancel()` + `CancelAction` + grace wait; late-result rejection by `cancelled` flag and version | `runtime._cancel`, `_execute` | `test_interrupt_cancels_and_rejects_late_result` |
| **New:** Tool manifests: JSON-schema args, `mutation_class`, `cancellable`, `idempotent`, `authorization`, `postcondition`; runtime registration; required-arg check | `tools.ToolManifest`, `ToolRegistry` | `test_manifest_registry.py` |
| **New:** Lineage-aware invalidation: nodes carry `input_fields` / `dependencies`; invalidation follows the real data flow (kind table kept only as a fallback) | `provenance.invalidate_affected`, `contracts.ExecutionNode` | `test_provenance.py` |
| **New:** Python 3.10 groundwork: `_compat.StrEnum`, `requires-python >=3.10,<3.13`, ruff `py310` | `_compat.py`, `pyproject.toml` | (still blocked by `datetime.UTC`; see B-P0-4) |
| **New:** Dockerfile (python:3.12-slim) | `Dockerfile` | runs the test suite |
| Frozen Pydantic v2 contracts for all 9 pipeline stages | `contracts.py` | `test_contract.py` (22) |
| Versioned state V1→V2→V3, WAL option, `merge_rapid` burst merge | `versioned_state.py` | `test_versioned_state.py` |
| Stale gate: late old-version results → `DISCARD` | `provenance.py` | ablation: 2 stale results leak without it |
| Risk tiers FREE / STAGEABLE / MUTATING / IRREVERSIBLE; CommitGate ALLOW / STAGE / CONFIRM_REQUIRED / BLOCK | `policy.py` | `test_policy.py` |
| Effect ledger: `sha256(version+tool+args)` keys, never re-dispatch COMMITTED, `verify_after_timeout` | `ledger.py` | 10-case timeout matrix, zero double-books |
| Duplicate result delivery applied once | `replay.py` | `duplicate_result` scenario |
| Async mock tool sandbox (real `asyncio.sleep`, cancellable, `external_operation_id`; now falls back to manifests) | `tools.MockToolSandbox` | `test_tools.py` |
| Plan DAG generator with `ref(step.field)` chains; `affected_steps` for re-planning | `planner.py` | `test_planner.py` |
| Branch manager: ≤2 shadows, depth ≤3, ≤6 calls, FREE/STAGEABLE only, full lifecycle incl. ABANDONED | `branch_manager.py` | `test_branch*.py`; cleanup p95 ≈ 0.009 ms (spec target < 20 ms) |
| Shadow orchestrator + result store: real dispatch, query-matched promotion and reuse, cleanup | `orchestrator.py`, `shadow_store.py` | `test_shadow_integration.py`; slowdown 4.5% (cap 5%) |
| Deterministic stepped-clock replay, 7 scenarios; naive baseline; ablations (stale gate, shadows) | `replay.py`, `baseline.py`, `cli.py` | `reports/comparison.md`, `reports/ablation.md` |
| CLI + FastAPI preview (`/health`, `/replay/{id}`, `/metrics/*`) | `cli.py`, `api.py` | `make eval`, `make demo` |

---

## 4. Verified Defects in the New Runtime

These come from running `AgentRuntime` directly, not from reading the code alone.

| # | Probe | Observed | Why it matters |
|---|---|---|---|
| D1 | Default registry; chunks "Book Delhi flights " + "for next Monday morning" + EOT | 3 `speak` actions ("Got it — listening…" ×2, "Got it — on it…"), then `tool_call search_flights args: {}` | Excess fillers (Guide §3.2.1); **destination and time never reach the tool** (Task Completion) |
| D2 | Then "Actually, Bangalore" + EOT | `cancel` old call ✅, new `tool_call search_flights args: {}` | Cancellation works, but the new call is identical: the correction isn't carried through |
| D3 | `TOOL_RESULT` event with any `call_id` | Silently ignored; no action | The harness sends asynchronous tool results; the runtime runs tools locally instead and would never react to the harness's results |
| D4 | Manifests `create_ticket` (MUTATING) + `lookup_manual(model required)`; "my washer shows error E4" | `ValueError: missing required arguments for lookup_manual` raised out of `handle()` | Picks the first read-only tool regardless of intent; a missing slot crashes the loop instead of producing `clarify` |
| D5 | Register a manifest in session "a" | Visible to every session | Session isolation (Guide §6) |
| D6 | Any version change | `_cancel(s, list(s.calls))`: every call cancelled | Contradicts selective invalidation; still-valid work (e.g. a hotel search when only the flight time changed) is thrown away |
| D7 | Any state-changing manifest | Never selected (`choose_read_tool` only) | Booking and ticket creation can never complete through the harness path |
| D8 | Python 3.10 import | `from datetime import UTC` in `contracts.py`, `branch_manager.py` | `ImportError` on 3.10 despite the new `requires-python` |

---

## 5. What Needs to Be Built

Ordered by score impact within each part. **P0** means the harness can't score properly without it.

### PART A — AI/ML

| ID | Priority | Work | Acceptance criteria |
|---|---|---|---|
| A-1 | **P0** | **Slot binding into tool args.** Map arbiter deltas and state (`destination`, `time_constraint`, …) onto manifest argument names; for unseen tools, extract slots from the utterance using the manifest's JSON schema (LLM structured output with a rule fallback) | D1/D2 fixed: `search_flights` gets `{to: "Bangalore", slot: "morning"}`; unseen `lookup_manual` gets `{model: …}` from text or frame |
| A-2 | **P0** | **Filler policy.** At most one ACK per user turn (on EOT, or on early marker detection), no ACK per chunk, one progress line only when a tool runs > ~1.5 s, never "done" before a confirmed result | Unit test: ≤1 filler per turn; no completion claim without a `tool_result` |
| A-3 | **P1** | **ASR (A-R2/A-R3).** `faster-whisper` (base/small INT8) preloaded in the warm-up hook; word-level confidence → `asr_confidence`; clarify when a critical slot (city, date, ID) is below τ | Real WAV → transcript + confidence; low-confidence city → "Did you say Bangalore or Mangalore?" |
| A-4 | **P1** | **Grounded final response + real snapshot (A-R9).** `intent = {name, category, status}` separate from `slots`; text rendered from tool-result fields (flight no., ticket id, manual step); truthfulness check that every entity comes from a result or a slot | `FinalAction.snapshot.intent ≠ slots`; test fails if the response names an entity not in the evidence |
| A-5 | **P1** | **Domain-general arbiter (A-R7).** Pass the manifest list and current slots into the fused prompt so categories and deltas work for support, troubleshooting and in-car domains | Arbiter handles "change the priority to high" and "it's the other model" |
| A-6 | **P1** | **Held-out gold v2 (A-R8).** ~150 utterances not written against the regex table: non-travel domains, disfluencies, compound turns, ASR-noisy text. Gold v1 was built to be pattern-coverable, so its 100% says nothing about generalisation | Real-LLM accuracy + macro-F1 reported on v1 and v2 |
| A-7 | **P2** | **Vision grounding (A-R4/A-R5).** OCR (PaddleOCR/Tesseract) + small VLM (Florence-2 / BLIP) → device model, error code, indicator state; ambiguity (several objects, low OCR confidence) → clarify; ambiguous frames block risky actions | Real PNG → grounded `model` / `error_code` slots → `manual_lookup` args |
| A-8 | **P2** | **Cross-modal fusion (A-R6).** Merge `[audio: …]` and `[vision: …]` evidence into one arbiter call; multimodal output stays evidence, never execution authority | "What does this light mean?" + frame → grounded manual lookup |
| A-9 | **P2** | **Streaming perception (A-R1).** Run Layer 0 on every chunk for early interruption and ACK; mutate critical state only on EOT or on high-confidence markers | Time-to-first-ACK measured from the chunk timestamp |
| A-10 | **P2** | **Self-repair and disfluency.** "to Del— uh, Bangalore" and restarts inside one turn (accessibility use case) | Gold v2 disfluency slice ≥ 90% |
| A-11 | **P2** | **IVS.** Define the metric (not defined anywhere in the repo), compute it in the arbiter, and pass `ivs_score` to policy as in `/api/v1/intent/classify` | Written definition + unit tests; CommitGate reads it |
| A-12 | **P3** | **Real-model evaluation.** Run gold v1/v2 with Gemini / OpenAI / Ollama keys; replace the simulated latency offsets in reports | Reports marked "real" with model id and date |
| A-13 | **P3** | **Unnecessary-clarification rate** (Spec §9, target < 5%) | Metric in eval output |

### PART B — Agentic

| ID | Priority | Work | Acceptance criteria |
|---|---|---|---|
| B-1 | **P0** | **Connect `AgentRuntime` to the kernel.** Per turn: arbiter → `VersionedStore.patch` → `planner.generate_plan` / `affected_steps` → `ProvenanceGraph` nodes with `input_fields` → `CommitGate.can_execute` → ledger → dispatch. Cancel only calls whose nodes `invalidate_affected` returns | D6 fixed: an unrelated in-flight call survives a correction; Delhi call cancelled, morning filter kept |
| B-2 | **P0** | **Harness-driven tool execution.** In harness mode, emit `tool_call` and wait for `TOOL_RESULT` events (match by `call_id`, run through the stale gate, update the node, continue the plan or send `final`). Keep `MockToolSandbox` only for local replay / tests | D3 fixed: a harness result for a live `call_id` → `final`; a result for a cancelled or older-version call → ignored and logged |
| B-3 | **P0** | **Event and action timestamps.** Add `ts` / `at_ms` to `RuntimeEvent` and to every action; take time from events (virtual clock), not the wall clock | Pivot latency and ACK latency computable from the trace alone |
| B-4 | **P0** | **Error handling in `handle()`.** Missing required args → `ClarifyAction` naming the slot; tool exceptions → honest message + retry policy (B-8); `serve()` must never die on one event | D4 fixed; fuzz test with malformed events keeps the loop alive |
| B-5 | **P0** | **Python 3.10.** Replace `from datetime import UTC` with `timezone.utc` (`contracts.py`, `branch_manager.py`); grep for other 3.11+ APIs; CI matrix 3.10 / 3.11 / 3.12 | `pytest` green on all three |
| B-6 | **P0** | **Align with the official kit when it is released.** Map the organizer's event and action field names onto `RuntimeEvent` / `RuntimeAction`; run the 9 public scenarios end to end | 9/9 public scenarios run; trace saved |
| B-7 | **P1** | **Intent-aware generic planner (B-R5).** Choose tools by intent + available slots + manifest description, not "first read-only tool"; allow state-changing tools through the CommitGate; chain calls via `ref()` | D7 fixed: `create_ticket` called after the slots are filled; booking requires confirmation |
| B-8 | **P1** | **Retry policy.** Read-only: bounded retry with backoff on injected faults. State-modifying: `verify_after_timeout` via a status tool from the manifest (`postcondition`), never a blind retry | Fault-injection scenario: search retried ✓; booking verified, not re-sent |
| B-9 | **P1** | **Idempotency across re-plans.** Key state-changing calls on `effect_id_for(tool, normalised args, session)`, not on `call_id`; ledger check before every outgoing mutating `tool_call` | Zero duplicate mutations across interruption, retry and shadow promotion (automated assertion) |
| B-10 | **P1** | **Manifest hardening.** `mutation_class: Literal["READ_ONLY","STAGEABLE","MUTATING","IRREVERSIBLE"]`; map to `RiskLevel`; honour `cancellable` (non-cancellable → ABANDONED + stale gate) and `authorization` (→ CONFIRM_REQUIRED); validate args against the full JSON schema (types, enums) | Invalid manifest rejected; unauthorised irreversible call blocked |
| B-11 | **P1** | **Session isolation.** Per-session registry overlay, sandbox dedup, ledger, branch manager and shadow-scorer history; clear on session end | D5 fixed; test that two sessions share no state |
| B-12 | **P1** | **Shadows on the harness path.** Run bounded read-only shadows from `AgentRuntime` via `orchestrator`; promote only if `base_version == current_version` (Spec §10 demo 3) | Demo 3 runs through the queues |
| B-13 | **P1** | **Cancellation metrics.** Log `cancel_latency_ms`, count superseded calls not cancelled, flag reruns with stale args | Metrics in the trace; cancel within the grace period asserted in tests |
| B-14 | **P2** | **Burst coalescing on the live path.** Debounce EOTs inside `MERGE_WINDOW_MS` before dispatching heavy work (`merge_rapid`) | `rapid_burst` → 1 dispatched search, not 3 |
| B-15 | **P2** | **`/api/v1` endpoints (Spec §8).** `intent/classify`, `plan/generate`, `effect/verify` wrapping the kernel with the listed fields | Contract tests per endpoint |
| B-16 | **P3** | **Warm-up hook + watchdog.** `setup()` preloads MiniLM, Whisper and OCR/VLM within 300 s; per-scenario watchdog sends a safe `final` before 120 s | Timed test for both |
| B-17 | **P3** | **Packaging.** Dockerfile `CMD` runs the agent entrypoint; build images or a lockfile for 3.10–3.12; one documented command from a clean clone | Clean-clone run passes |
| B-18 | **P3** | **Regression suite.** Port the public scenarios; assert trace-level metrics (cancel latency, duplicates = 0, snapshot accuracy, stale-action rate = 0) | CI job |

### Frontend / demo (Spec §11)

| ID | Priority | Work |
|---|---|---|
| F-1 | P2 | DAG + branch lifecycle view driven by runtime traces (cancel, stale reject and shadow promotion must be visible) |
| F-2 | P2 | Metrics HUD: pivot latency, ACK latency, stale-action rate, shadow reuse / waste |
| F-3 | P3 | 5-minute demo video following Spec §10; update `docs/DEMO_SCRIPT.md` to use the runtime path |

---

## 6. Evaluation Plan Coverage (Updated Spec §9)

| Metric | Target | Measured today? | Where / what's needed |
|---|---|---|---|
| Task completion | maximize | 🟡 | Baseline comparison "correct" flag on 7 travel scenarios; nothing on the runtime path |
| Interruption recovery | core | 🟡 | Replay traces; needs runtime-path scoring |
| Pivot latency (interrupt → new valid task) | median < 250 ms, report P95 | ❌ | Needs event timestamps (B-3) |
| Acknowledgement latency | fast | ✅ kernel / ❌ runtime | `fast_ack_latency_ms`; runtime actions have no timestamps |
| Stale-action rate | 0 | 🟡 | Baseline defect flag + ablation; no runtime metric |
| Duplicate mutation | 0 | ✅ kernel | Timeout matrix; runtime not covered (B-9) |
| Regretted irreversible (commit then retract/modify) | 0 | ❌ | New counter over traces |
| Selective recompute (useful work preserved) | higher is better | ✅ replay | `delhi_bangalore`: 1 of 3 invalidated, 2 reused |
| Shadow reuse vs waste | measure | ✅ | `reports/shadow_metrics.md` (50 / 50) |
| Cleanup time | < 20 ms | ✅ | p95 ≈ 0.009 ms in-process |
| Unnecessary clarification | < 5% | ❌ | A-13 |
| Baseline: replan-from-scratch agent | — | ✅ | `baseline.py`, `reports/comparison.md` |
| Ablation: provenance gating | — | ✅ | `cli ablate` (stale gate) |
| Ablation: shadow execution | — | ✅ | `cli ablate` |
| Ablation: commitment policy | — | ❌ | Add a `disable_commit_gate` flag |
| Ablation: effect verification | — | ❌ | Add a `disable_verify` flag (expect double-books) |

## 7. Demo Sequence Readiness (Updated Spec §10)

| Demo | Replay path today | Via harness runtime | Missing |
|---|---|---|---|
| 1. Pivot while work runs (Delhi → Bangalore, keep morning, cancel, stale reject) | ✅ except **explicit cancel** (replay only discards the stale result) | 🟡 cancel ✅, args empty, morning lost | B-1, A-1; emit `cancel` in replay too |
| 2. Retraction ("Don't book it, just show options") | ✅ `dont_book_it` | ❌ runtime never books | B-1, B-7 |
| 3. Counterfactual reuse (shadow promoted only if `base_version == current_version`) | ✅ `shadow_bangalore`; the version-equality guard isn't explicit in `promote_or_discard` | ❌ | B-12; add the explicit guard |
| 4. Timeout → UNKNOWN → verify → reuse / safe retry | ✅ `timeout_booking` (enum has `FAILED` / `ROLLED_BACK`, no `NOT_COMMITTED`) | ❌ | B-8 |

## 8. Use-Case and Test-Mix Coverage (Guide §2, §4)

| Use case / test type | Share | Status |
|---|---|---|
| In-car: drop stale routes when the destination changes | — | ✅ flight analogue in replay; 🟡 runtime |
| Support: change parameters mid-booking, no double-booking | — | ✅ replay; ❌ runtime (no mutating calls) |
| Field troubleshooting: camera frames + manuals | — | ❌ (A-7, B-7) |
| Accessibility: hesitations, self-repair | — | 🟡 (A-9, A-10) |
| Text scenarios | 50% | 🟡 |
| Audio scenarios | 30% | ❌ functionally: every real WAV → clarify (A-3) |
| Visual scenarios | 20% | ❌ functionally: every real PNG → clarify (A-7) |
| Unseen tools | — | 🟡 registration ✅, planning ❌ (B-7) |
| Chained calls / retries / clarifications | — | 🟡 kernel ✅, runtime ❌ |

---

## 9. Ownership (Updated Spec §11)

| Owner | Next items |
|---|---|
| **Backend** | B-1, B-2, B-3, B-4, B-5, B-6 (P0) → B-9, B-10, B-11, B-13 → B-14 – B-18 |
| **AI/ML A** (perception, arbiter, dialogue, eval) | A-1, A-2 (P0) → A-3, A-4, A-5, A-6 → A-7 – A-13 |
| **AI/ML B** (planner, tools, shadows) | B-7, B-8, B-12, plus the commit-gate and verify ablations |
| **Frontend** | F-1 – F-3 |

## 10. Final Definition of Done (Updated Spec §12) — Current State

- [x] Two-queue harness accepts events and emits schema-valid actions (skeleton; must follow the official schema once released, B-6)
- [~] Every tool call has a `call_id` ✅; superseded primary calls receive `cancel` ✅, but so do still-valid ones, and cancel latency isn't measured (B-1, B-13)
- [x] Late obsolete results cannot change current state (stale gate + runtime `cancelled` / version check)
- [ ] Zero duplicate state-changing calls **on the runtime path** (B-9)
- [ ] Manifest-defined unseen tools can be planned and called with valid arguments (registration done; planning and arg extraction not: B-7, A-1)
- [~] Every final response carries an `{intent, slots}` snapshot (present, but `intent == slots`: A-4)
- [ ] Audio input produces a transcript + confidence and clarifies ambiguous critical slots (A-3)
- [ ] Vision input grounds device / manual info and clarifies ambiguity (A-7)
- [ ] Final responses are grounded and never claim completion without evidence (A-2, A-4)
- [~] Session state isolated between scenarios (store / calls yes; registry / dedup no: B-11)
- [ ] Python 3.10 / 3.11 / 3.12 verified (B-5)
- [ ] Warm-up and scenario watchdog verified (B-16)
- [~] Docker / clean-clone execution from one command (Dockerfile runs tests only: B-17)
- [ ] 5-minute demo shows interruption, cancellation, stale rejection, selective reuse and shadow promotion (F-3, via B-1 / B-12)

## 11. Documentation Drift to Fix

- `README.md`: 137 tests / Python 3.11 / old repo URL and branch → 171 tests, 3.10–3.12, `main`.
- `docs/STATUS.md`: stops at Phase 4 (165 tests); doesn't mention `runtime.py`, manifests or multimodal intake.
- `perception.py` module docstring still says "Embedding gate + ASR are Phase 2".
- `ledger.py` docstring says "Phase 3 will add tool-client verify hook"; `verify_after_timeout(exists_fn=…)` already exists.
- `Dockerfile` has no comment saying it runs the tests rather than the agent.
- `docs/THEME05_SPEC_TRACKER.md` (untracked) predates `a874d78` / `b227b25`; delete it or point it to this file.
