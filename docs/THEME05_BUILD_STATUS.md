# CONTINUUM — Theme 05 final build status

> **Updated:** 30 Sep 2026 (follow-up release audit)
>
> **Verification:** fresh locked core environment: **331 passed, 8 optional skips**;
> FDB environment: **334 passed, 5 optional local-recognizer skips**; Ruff and
> mypy clean (50 source files). See [`SUBMISSION_AUDIT.md`](SUBMISSION_AUDIT.md)
> for exact commands, external gates, and the FDB dispatch repair.
>
> **Reproduce:** `make test && make lint && make eval-b`; for real ASR/OCR on a
> Linux host install `libgl1 libglib2.0-0`, then `pip install -e '.[multimodal]'`,
> `continuum fetch-models`, and `continuum eval-multimodal`. The ASR download is
> a one-time network setup step, never a request-time download.

## Specification audit — 29 Sep 2026

Every requirement of the Theme 05 guide and the updated specification's definition of done, checked against the running code on `main`:

| Requirement (source) | Status | Evidence |
|---|---|---|
| Two async queues, events in / actions out (guide §3) | ✅ | `AgentRuntime` queues; `continuum kit` JSONL bridge (aliases, base64/data-URI media, malformed input → clarify) |
| Text chunks + end-of-turn (guide §3.1) | ✅ | `streaming.py` buffers chunks; one ACK per turn at EOT |
| **Raw WAV audio** (guide §3.1, 30% of scenarios) | ✅ **new** | Local faster-whisper (`base.en`), calibrated confidence, recognition off the event loop; 3 raw-audio scenarios at 100 |
| **Raw PNG frames** (guide §3.1, 20% of scenarios) | ✅ **new** | Local RapidOCR grounds model/error code from pixels; textless and blurred frames clarify; 4 raw-frame scenarios at 100 |
| Interruption signals, cancel with `call_id` in grace (guide §3.2.2) | ✅ | Named call cancelled immediately; content-based plan-diff cancel p95 ≈ 1.8 ms text / ≈ 0.9 s when the correction must first be transcribed |
| Async tool results, scenario manifests, unseen tools (guide §3.1, §4) | ✅ | External mode matches `tool_result` by `call_id`; planner held-out 96.4% on unseen domains |
| Session slots + localized corrections (guide §3.2.3) | ✅ | Versioned slot memory; reconcile adopts unchanged calls |
| Read-only vs state-modifying, zero duplicate mutations (guide §3.2.4) | ✅ | Manifest tiers, effect keys, verify-after-timeout; 0 duplicates in every suite (ablation reproduces the double booking) |
| Multimodal grounding + clarify ambiguous perception (guide §3.2.5) | ✅ **new** | Low-confidence ASR/OCR is read back ("I heard 'Actually, Mangalore.' … is that right?"), never executed |
| Protocol compliance (guide §3.2.6) | ✅ | Pydantic-validated actions, unique call ids, snapshot `{intent, slots, version}`; stdout JSON now ASCII-escaped (encoding-proof) |
| Fast path, no false completion, no excess fillers (guide §3.2.1) | ✅ | ACK p95 ≈ 0.03 ms even during ASR/OCR; finals only after accepted results; ~1 speak per turn |
| Python 3.10–3.12, 120 s cap, 300 s warm-up (guide §6) | ✅ | `_compat` shims + vermin check; `kit --watchdog`; warm-up loads ASR/OCR from disk and reports them |
| Session-scoped memory (guide §6) | ✅ | Per-session registry, sandbox, slots, speculator |
| Docker / one-command run (spec §12) | ✅ *(not built here)* | Image installs `.[multimodal]` and its OpenCV system libraries; provide a pre-fetched `models/faster-whisper-*` directory at `/app/models` (or set `CONTINUUM_ASR_MODEL`) for raw-ASR runs. Docker is not available on the audit machine. |
| 5-minute demo (spec §12) | ⬜ outside code | `docs/DEMO_SCRIPT.md`; recording is a team task |

**Fixed in this audit:**
1. **Dense arbiter:** it reloaded MiniLM on every call. It is now cached once per process, which fixed the persistent 70 s test failure; its report is now a genuine 98.0%.
2. **ASR confidence:** it rejected 4 of 6 perfectly transcribed clips. It is now calibrated on word probabilities.
3. **Planner continuity:** it let a finished lookup swallow a new spoken request.
4. **OCR on a truncated frame:** it raised an exception; it now clarifies.
5. **CLI on the Windows console:** it crashed on "→" (cp1252); it now reconfigures to UTF-8.
6. **Kit JSONL:** it emitted raw UTF-8 that arrived garbled; it now emits ASCII-escaped JSON.
7. **FDB smoke runner:** it used POSIX-only `killpg`/`SIGKILL`, which crash on Windows.
8. **Local `google-genai`:** 2.3.0 lagged the locked 2.25.0; it is now aligned.

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
- Raw media is recognised locally: speech by faster-whisper (the model is
  discovered via `$CONTINUUM_ASR_MODEL` or `models/faster-whisper-*`), frames
  by RapidOCR (models inside the wheel). `continuum fetch-models` is the only
  command that downloads anything. Warm-up loads both from disk.
- Recognition runs in a worker thread after an immediate "Listening…" /
  "Looking at that…" ACK, so in-flight tools and cancels are never stalled.
- ASR confidence is the mean Whisper *word* probability, capped at 0.60 when
  any word is below 0.30 and zero for likely non-speech. `exp(avg_logprob)`
  sat at 0.61–0.78 on perfect transcripts and failed most clean turns. On
  6 fixtures × {clean, 5 dB, −5 dB}, the rule accepts every clean clip and no
  wrong transcript. It is conservative: some correct noisy clips are read back.
- Unreadable or low-confidence media is read back and never executed: a
  noisy "Actually, Bangalore" recognised as "Actually, Mangalore." (0.43)
  keeps the Delhi search running and asks.
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
| **Raw media, real ASR + OCR — CONTINUUM** | **100.00** | 0 | 0 |
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
continuum eval-multimodal             # reports/multimodal_eval.* + raw_media_eval.* (real ASR/OCR)
continuum fetch-models                # one-time: faster-whisper base.en → models/ (setup only)
python scripts/make_media_fixtures.py # regenerate data/media/ (speech needs Windows SAPI)
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
2. Real ASR/OCR now run end to end, but on 9 synthetic fixtures (one TTS
   voice, rendered panels). They show that the pipeline and the confidence
   gating work; they are not an accuracy benchmark for accented speech or
   real camera photos. The calibration thresholds come from those same
   fixtures.
3. The rule-based arbiter still has travel-era vocabulary. The generic planner
   compensates by using manifests and schema evidence, but a domain-general
   learned arbiter remains future work.
4. Runtime plan-diff identity and stale gates enforce result validity; full
   `ProvenanceGraph` population for every planner step is not yet exposed in
   organizer snapshots.
5. Burst turns are correct but are not coalesced before every dispatch.
