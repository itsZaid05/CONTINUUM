# AI/ML Engineer B — Planning, Tools, Speculation & Evaluation

> **Scope (updated spec §11):** planner DAG, tools, shadow predictor/manager, speculation metrics. Next-value items from the spec: **manifest-driven generic planning** (B-R4/B-R5), retries (B-8), shadows on the harness path (B-12), plus a proper evaluation pipeline.
> **Status (26 Sep 2026):** final harness-edge build, tested (246 tests pass), evaluated. Reproduce planner, text runtime, and multimodal reports with `make eval-b` (offline; no API keys).
> **29 Sep 2026:** continuity is now granted to the goal in progress only when the turn fits it (supplies its arguments, is an explicit correction, or fits no other tool). This was found on the new raw-media suite, where a finished `lookup_manual` swallowed a spoken "repair it on Monday" request. Planner dev/held-out scores are unchanged (1.000 / 0.964, same single miss h22). 318 tests pass.

---

## 1. What was built

| Piece | Module | What it does |
|---|---|---|
| **Tool manifests** | `tools.py` | `ToolManifest` gains `description`, `keywords`, `returns` (result fields, used for chaining). `mutation_class` is normalised across vocabularies (`read_only: true`, `state_modifying: true`, `"state-modifying"`, `WRITE`, …) onto the four CommitGate tiers; unknown values are rejected; `kind` and `risk` are derived from the tier. The built-in travel tools now carry real argument schemas. `load_manifests()` reads a manifest file. |
| **Mock environment** | `tools.py` | Deterministic **fault injection** per tool (`FaultPlan`: fail N times, time out N times, optionally *commit on timeout*); a `status()` verify hook; a dispatch log and effect log; deterministic payloads synthesised from a manifest's `returns` schema, so chained calls work for tools never seen before. |
| **Slot extraction** | `slots.py` | Fills arguments from each tool's own JSON Schema: enum (generic values like "high" need the field name nearby), pattern (unanchored; conflicts resolved by cue words: "error **E12**" vs model "**DW-220**"), date (relative, weekday, ISO, "26 Oct"), time, integer (units, "for 4", range-checked), place (prepositions, origin/destination, a city gazetteer, bare corrections), free text (the utterance minus its command clause). Slot memory resolves aliases (`city` inherits `to`). |
| **Generic planner** | `generic_planner.py` | Ranks every manifest tool against the turn (topic overlap with synonym folding, slot evidence, verb class, continuity, negation). Handles new goal / correction / additive / replace / retraction / backchannel / clarification answers. **Chains** missing required arguments through tools whose `returns` provide them (READ_ONLY/STAGEABLE producers only). **Gates** IRREVERSIBLE steps behind an explicit commit verb or a confirmation, and asks for missing required arguments instead of guessing. Deterministic, sub-millisecond. |
| **Harness execution** | `runtime.py` | `AgentRuntime` now runs every turn through the planner and a **reconcile → advance → execute** loop: in-flight calls whose (tool, args) survive the new plan are *adopted* (no cancel, no rerun); everything else gets `task.cancel()` + `cancel{call_id}` inside the grace window. Identical read results are reused; state-changing calls are keyed on (session, tool, args), so they land at most once. Read-only retries use backoff and new call ids. A timed-out mutation is **verified** before anything is re-sent. Late results for superseded calls are rejected. IRREVERSIBLE steps wait for "yes". The final action carries `snapshot{intent, slots, version}`. |
| **External tool mode** | `runtime.py` | `tool_mode="external"`: emits `tool_call`, waits for harness `tool_result` events matched by `call_id`, and ignores results for cancelled or superseded calls. A state-changing timeout here is reported as *uncertain* and never blindly retried (there is no verify channel). |
| **Session isolation** | `runtime.py` | Per-session tool registry, sandbox (dedup + effects), slots, goals, ledger and speculator. A scenario's first `manifest` event replaces the built-in travel tools for that session only. |
| **Generic speculation** | `speculation.py` | Read-only shadows from two planner signals: *hedge* (a second reading of an argument) and *prefetch* (a read-only tool fully answerable from memory that shares arguments or domain with the goal). Budget and lifecycle come from the existing `BranchManager`. A shadow is promoted only on an exact (tool, args) match and cancelled/cleaned when its premise changes. Measures reuse, waste and hidden latency. **Off by default** (see §4.3). |
| **Evaluation** | `evaluation/planner_eval.py`, `evaluation/runtime_eval.py` | See §3. CLI: `continuum eval-planner`, `continuum eval-runtime`; Makefile: `make eval-b`. |

Smaller supporting changes: `PlanStep.risk_level` (a manifest tier overrides the kind table), `VersionedStore.apply_slots` (slot memory becomes versions; a no-op turn does not bump the version), `GenericPlanner.replan` (rebuild a plan from memory, e.g. after "no" to a confirmation), and a crash guard in `run_once` so one bad event never stops `serve()`.

### Defects from `THEME05_BUILD_STATUS.md §4` — now fixed

| # | Defect | Now |
|---|---|---|
| D1 | Tool args empty; 2–3 fillers per turn | Args bound from schema (`{to: Delhi, date: 2026-09-28, slot: morning}`). *Fillers are unchanged — Engineer A's A-2.* |
| D2 | Correction re-dispatched the same empty call | Cancel + re-dispatch with the corrected slot; other slots kept |
| D3 | Harness `tool_result` ignored | Consumed in `tool_mode="external"` |
| D4 | Unseen tool with a missing arg crashed `serve()` | Clarification naming the missing slot; the loop is also crash-guarded |
| D5 | Manifests leaked across sessions | Per-session registries |
| D6 | Any change cancelled all in-flight work | Plan-diff reconcile: unchanged calls are adopted |
| D7 | State-changing tools never selected | Selected on write intent; gated by risk tier |

---

## 2. Design decisions worth knowing

1. **The arbiter's category is used; its slot delta is not.** The offline arbiter is travel-only: it labels "Find flights to Delhi tomorrow morning" as `NOISE (0.60)` and writes whole sentences such as "My Router Keeps Dropping Wifi," into `destination`. The planner therefore takes slots from `slots.py` and only honours `NOISE ≥ 0.9` (backchannel), `RETRACT ≥ 0.72` and the arbiter's own clarification gate. **Engineer A:** a domain-general arbiter (A-5) would let the planner use the delta again.
2. **Producers are never state-changing.** A plan will not create a ticket just to get a `ticket_id`. Ids produced by committed effects enter session memory from results instead.
3. **Irreversible actions need an explicit verb in the user's own turn** ("book", "order", "delete", "go ahead") or a "yes" to a confirmation. MUTATING and STAGEABLE steps run when the turn asks for a change (CommitGate semantics).
4. **A bare barge-in does not cancel work.** What gets cancelled is decided by what the user says next (plan diff). An `interrupt` naming a `call_id` still cancels that call immediately.
5. **Retraction after commit is honest.** "Don't book it" after the booking landed produces the dialogue manager's "already booked (ref) — want me to cancel?" message, never a fake undo.
6. **Speculation is local and opt-in.** Shadows are never emitted as harness `tool_call`s, so they cannot add unrequested calls to the scored action stream.

---

## 3. Evaluation pipeline

### 3.1 Planner gold set — `data/gold/planner_gold.jsonl`

- **65 turns**, hash `e92fa9a38623`, reference date 2026-09-25. Each turn gives the manifests in scope, session memory (`slots`, `active_goals`), the utterance, and the correct plan: action, goal tool, tool sequence, exact args, substring args for free text, missing args, whether it is state-changing, whether confirmation is needed.
- **Protocol:** the gold set and the manifest catalog (`data/manifests/*.json`: support, troubleshooting, in-car, dining, home) were written and hashed **before any planner code existed**. Development used **dev** only (37 turns: travel, support, troubleshooting, in-car). **Held-out** (28 turns: the unseen *dining* and *home/calendar/weather* domains plus new phrasings of dev domains) was run **once**, after development stopped; that first run is kept verbatim in `reports/planner_heldout_first_run.json`. The one held-out failure was not tuned away.
- Each turn runs through the **real runtime path** (offline arbiter → planner), so arbiter mistakes count.
- **Metrics:** fully-correct turns, action accuracy, goal accuracy, plan exact match, argument P/R/F1, clarification P/R, unnecessary-clarification rate, confirmation accuracy, **unsafe state-changing plans** (target 0), crashes, planning latency. Reported by split and by domain.
- **Baseline:** the runtime's pre-planner path (`choose_read_tool` + `bind_args`).

### 3.2 Runtime scenarios — `data/runtime_scenarios/text_suite.json`

- **18 timed text scenarios**: interruption / pivot (5), retraction before and after commit (2), injected faults (timeout committed, timeout not committed, read failure ×2), unseen tool domains (3), clarification and confirmation (3), state-changing corrections in flight (2).
- Replayed through `AgentRuntime` with event times and tool delays scaled together (×0.1). Scored from the action stream plus the runtime trace in the **four Theme 05 categories with the guide's weights (40/35/15/10)**. This is our approximation; the official scorer isn't released.
- **Extra metrics from updated spec §9:** stale-action rate, stale reruns, duplicate mutations, regretted irreversible actions, unnecessary clarification, pivot latency p50/p95, ack latency, cancel latency, tool calls, speculation reuse/waste.
- **Compared systems:** `continuum`; `continuum+speculation`; `naive_runtime` (the runtime before this work); and four single-mechanism **ablations**: verify-after-timeout, selective cancel, read retry, commit gate.

---

## 4. Results (25 Sep 2026, `make eval-b`)

### 4.1 Planner (`reports/planner_eval.md`)

| Metric | dev (37) | **held-out (28)** | baseline (all 65) |
|---|---|---|---|
| Fully correct turns | 1.000 | **0.964** | 0.062 |
| Goal-tool accuracy | 1.000 | **0.963** | 0.333 |
| Argument F1 | 1.000 | **0.979** | 0.224 |
| Clarification precision / recall | 1.0 / 1.0 | **1.0 / 1.0** | 0 / 0 |
| Unnecessary clarification | 0% | **0%** | 0% |
| Unsafe state-changing plans | 0 | **0** | 0 |
| Crashes | 0 | **0** | 45 |
| Planning latency p95 | 1.3 ms | **0.5 ms** | — |

The two domains never seen during development (dining, home) are **16/16** correct. The single held-out miss is **h22** ("Add a note to TCK-5120 saying the customer called back"): "add" reads as a *create* verb, so `create_ticket` beat `update_ticket`. Adding to an existing record is a known ranking weakness.

Dev at 100% is *not* evidence of generalisation, because it was used for development. Held-out is the number to quote.

### 4.2 Runtime (`reports/runtime_eval.md`)

| System | Score | Task | Interrupt | Safety | Dup. mutations | Regretted irreversible | Tool calls |
|---|---|---|---|---|---|---|---|
| **continuum** | **100.0** | 1.00 | 1.00 | 1.00 | 0 | 0 | 35 |
| naive runtime (before) | 54.7 | 0.13 | 0.70 | 1.00 | 0 | 0 | 22 |
| − verify-after-timeout | 99.7 | 1.00 | 1.00 | 0.97 | **1 (double booking)** | 0 | 36 |
| − selective cancel | 98.7 | 1.00 | 0.96 | 1.00 | 0 | 0 | 37 (+2 restarts) |
| − read retry | 97.8 | 0.94 | 1.00 | 1.00 | 0 | 0 | 33 |
| − commit gate | 96.7 | 0.92 | 1.00 | 1.00 | 0 | **1** | 36 |

Pivot latency (end of turn → corrected `tool_call`) is p50 about 1.1 ms and p95 about 3.4 ms in-process. Cancel latency is p95 about 1.8 ms, inside the 50 ms grace window.

Every ablation breaks exactly the scenario built to exercise it, so each mechanism is doing real work. **Read the 100 carefully:** this suite was written alongside the runtime, so it is a regression bar and a mechanism check, not an independent estimate. The held-out planner number is the generalisation evidence.

### 4.3 Multimodal runtime (`reports/multimodal_eval.md`)

The 11 deterministic audio/frame scenarios exercise grounded upstream ASR/OCR,
low-confidence clarification, interruption, unseen manifests, and irreversible
confirmation. CONTINUUM scores **100.00** versus **81.15** for the naive
runtime, with zero duplicate mutations and zero regretted irreversible calls.
This is also a regression suite, not a benchmark of an ASR or OCR model.

### 4.4 Speculation — an honest negative

Across the suite: 18 shadows spawned, **1 promoted** (5.6% reuse), 17 discarded, about 32 ms of tool latency hidden versus about 1.55 s of shadow tool time wasted (scaled clock). Prefetch fires on nearly every read goal, but users rarely ask for the prefetched tool next. **Keep `speculation=False`** (the default) unless the backend is cheap and follow-up requests are predictable. Tightening prefetch (for example, requiring two shared arguments, or learning co-occurrence from logs) is the obvious next experiment. It was not tuned here, to avoid fitting the suite.

### 4.5 How to read the scores

- **Latency saturates at 1.0 for every system:** the spoken ACK is a template emitted about 0.1 ms after end of turn. The category will only discriminate once an LLM sits in the path.
- **The rubric under-weights safety:** a double booking costs 0.28 points (safety is 10%, split across four checks). The duplicate-mutation and regretted-irreversible counts are the columns to watch, not the score.

---

## 5. Not covered / next

| Item | Owner | Note |
|---|---|---|
| Official unpublished field variants | Integration | `harness_edge.py` maps the documented/common aliases; add any organizer-only spellings if its kit differs |
| Real ASR/OCR model quality | AI/ML A | Optional disk-only faster-whisper is supported; deterministic evaluation uses upstream transcript/OCR evidence |
| Rich progress narration | AI/ML A | Floor control budgets one fast ACK per turn (`speaks_per_turn = 1.06` including failure updates); long-operation progress narration is not synthesized |
| Domain-general arbiter | AI/ML A (A-5) | Would let the planner trust arbiter deltas and stop the NOISE-on-real-request cases |
| "Add a note" → update vs create | AI/ML B | Needs an "existing record in memory + add" rule, validated on a *new* held-out set |
| Burst coalescing (`merge_rapid`) on the live path | Backend (B-14) | Rapid corrections currently cancel and re-dispatch each time (correct, but 3 calls instead of 1) |
| Provenance graph on the runtime path | Backend (B-1) | The runtime uses plan-diff identity for invalidation; `ProvenanceGraph` is not yet fed from it |
| Real-clock / virtual-clock hygiene | Backend (B-9) | Latency is wall-clock in-process; event `ts_ms` is echoed in the trace for the harness |

## 6. Reproduce

```bash
make eval-b                                  # planner + text + multimodal reports, no keys
python -m continuum.cli eval-planner         # reports/planner_eval.{json,md}
python -m continuum.cli eval-runtime         # reports/runtime_eval.{json,md}  (--no-ablations for speed)
python -m continuum.cli eval-multimodal      # reports/multimodal_eval.{json,md}
python -m pytest -q                          # 246 tests
```

On a Windows console, set `PYTHONIOENCODING=utf-8` for the older `replay`/`compare` commands (they print `→`; this is pre-existing and unrelated to this work).
