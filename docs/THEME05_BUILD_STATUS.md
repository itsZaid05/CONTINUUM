# CONTINUUM — Theme 05 final build status

> **Updated:** 26 Sep 2026
>
> **Verification:** 246 tests; Ruff and mypy clean; Python 3.10–3.12 CI
>
> **Reproduce:** `make test && make lint && make eval-b`

The earlier audits correctly identified that CONTINUUM's kernel was strong but
the organizer-facing edge was incomplete. The scored path now joins the
runtime, manifest planner, tool executor, safety gates, and multimodal
perception. The pre-implementation audit remains in Git history; this document
is the current source of truth.

## Readiness

| Scoring capability | Status | Evidence |
|---|---|---|
| Task completion | ✅ | Manifest-driven goals, schema slots, DAG chaining, retries, grounded finals; `runtime_eval` task score 1.0 |
| Interruption recovery | ✅ | Plan-diff adoption, selective cancellation, stale-result rejection, refreshed cancel snapshots; score 1.0 |
| Response latency | ✅ deterministic path | Timestamped events/actions, one fast ACK per turn; text and multimodal latency scores 1.0 |
| Safety and protocol | ✅ | CommitGate, atomic idempotency, verify-after-timeout, effect records, unique call ids, validated JSON actions; score 1.0 |
| Harness edge | ✅ | `continuum kit`; aliases, malformed input recovery, warmup, external tool results, watchdog final |
| Audio/frame input | ✅ grounded adapter | WAV/PNG validation, upstream ASR/OCR, confidence clarification, optional disk-only ASR; 11 deterministic scenarios |
| Python support | ✅ | Compatibility shims and CI matrix for 3.10, 3.11, 3.12 |

## Organizer contract

`AgentRuntime` owns two asynchronous queues and isolated per-session state.
The canonical input union is:

- text chunk and end-of-turn;
- WAV audio and PNG frame evidence;
- interrupt;
- asynchronous tool result; and
- one or more tool manifests.

The output union is `speak`, `tool_call`, `cancel`, `clarify`, and `final`.
Every action is timestamped. Calls and cancels use stable `call_id` values. A
cancel action carries the latest state snapshot; a final carries
`{intent, slots, version}` and names the tool result that grounds it.

`src/continuum/harness_edge.py` maps common organizer aliases onto this
contract and serializes JSONL over stdio. See [`HARNESS_EDGE.md`](HARNESS_EDGE.md).

## Planning and execution

| Requirement | Implementation |
|---|---|
| Unseen per-scenario tools | `ToolManifest`/`ToolRegistry`; aliases for input/output schemas and mutation vocabulary |
| Generic slot extraction | Argument JSON Schema drives enum, pattern, date, time, integer, place, and free-text extraction |
| Multiple goals and chaining | `GenericPlanner` builds steps and `ref(step.field)` dependencies from declared returns |
| Localized corrections | Session slots are versioned; reconcile compares `(tool, resolved args)` and adopts unchanged work |
| Cancellation | Superseded async tasks receive `task.cancel()` and an organizer cancel action within the grace window |
| Retry | Read-only failures retry with exponential backoff and fresh call ids |
| Timeout safety | State-changing timeout is verified locally; external uncertain effects are never blindly resent |
| Duplicate prevention | Stable effect keys plus an atomic per-key sandbox lock; committed effects are reused |
| Irreversible actions | Explicit authorization or confirmation is required unless running the documented ablation |
| Retraction | Pending work is pruned; a landed effect receives an honest already-committed response |
| Speculation | At most the bounded branch budget; read-only only, exact-match promotion, disabled by default |
| Provenance/staleness | Versioned state and lineage/stale gates remain in the kernel; the runtime additionally enforces plan/run identity before accepting a result |

## Perception and floor management

- Text chunks are buffered silently and get one fast acknowledgement at EOT,
  replacing the previous per-chunk plus per-turn fillers.
- Audio/frame transcripts carry calibrated confidence. Evidence below 0.72
  produces clarification and no tool call.
- Raw media with no transcript/OCR is never treated as execution authority.
- Optional `faster-whisper` requires an existing model directory and is loaded
  with `local_files_only=True`; no default command downloads a model.
- Completion language is emitted only after an accepted live tool result.
- On the scenario watchdog, the runtime cancels live calls and returns a
  truthful `timed_out` final without claiming an unconfirmed effect.

## Evaluation results

### Planner

Frozen set: 65 turns (37 development, 28 held-out).

| Metric | Dev | Held-out | Baseline overall |
|---|---:|---:|---:|
| Fully correct | 1.000 | **0.964** | 0.062 |
| Goal accuracy | 1.000 | 0.963 | 0.333 |
| Argument F1 | 1.000 | 0.979 | 0.224 |
| Unsafe write plans | 0 | 0 | 0 |

The first held-out run is preserved at
`reports/planner_heldout_first_run.json`; it was not tuned away.

### Runtime

| Suite/system | Score | Duplicate mutations | Regretted irreversible |
|---|---:|---:|---:|
| Text — CONTINUUM | **100.00** | 0 | 0 |
| Text — naive runtime | 54.65 | 0 | 0 |
| Audio/frame — CONTINUUM | **100.00** | 0 | 0 |
| Audio/frame — naive runtime | 81.15 | 0 | 0 |
| Text — no verify after timeout | 99.72 | **1** | 0 |
| Text — no CommitGate | 96.67 | 0 | **1** |

The runtime suites were authored with the implementation and should be read as
regression and mechanism checks, not independent estimates. Category weights
match the guide (40/35/15/10), but the official scorer was not released.

### Speculation

The opt-in speculation evaluation is intentionally negative: most prefetched
read-only branches are discarded and only a small fraction is promoted. It
therefore remains **off by default**. This preserves the mechanism without
claiming that wasted backend work is a production win.

## Commands and artifacts

```bash
continuum kit                         # JSONL stdin/stdout, external tools
continuum kit --local-tools           # deterministic sandbox
continuum eval-planner                # reports/planner_eval.{json,md}
continuum eval-runtime                # reports/runtime_eval.{json,md}
continuum eval-multimodal             # reports/multimodal_eval.{json,md}
make eval-b                           # all three evaluations
```

Relevant source and evidence:

- `src/continuum/runtime.py`
- `src/continuum/harness_edge.py`
- `src/continuum/generic_planner.py`, `slots.py`, `speculation.py`
- `src/continuum/tools.py`, `ledger.py`, `provenance.py`
- `src/continuum/perception.py`
- `data/manifests/`, `data/gold/planner_gold.jsonl`
- `data/runtime_scenarios/{text_suite,multimodal_suite}.json`
- `tests/test_harness_edge.py`, `tests/test_runtime_multimodal.py`
- `reports/planner_eval.*`, `reports/runtime_eval.*`, `reports/multimodal_eval.*`

## Known boundaries

1. The organizer's private kit/scorer is not in the repository. The edge is
   alias-tolerant, but private field names may require a small mapping update.
2. The checked multimodal score evaluates orchestration and confidence
   handling with provided evidence; it is not an ASR/OCR accuracy benchmark.
3. The rule-based arbiter still has travel-era vocabulary. The generic planner
   compensates by using manifests and schema evidence, but a domain-general
   learned arbiter remains future work.
4. Runtime plan-diff identity and stale gates enforce result validity; full
   `ProvenanceGraph` population for every planner step is not yet exposed in
   organizer snapshots.
5. Burst turns are correct but are not coalesced before every dispatch.
