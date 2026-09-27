# Phase 2 audit — FDB-managed LiveKit edge

**Date:** 27 Sep 2026  
**Upstream source:** `DanielLin94144/Full-Duplex-Bench`  
**Pinned revision:** `3e799c45a045256f47d5f1c9cda90157e2d2ec9e`  
**Selected provider:** Gemini native audio  
**Scope:** Provider boundary, LiveKit worker, FDB tool bridge/backend, telemetry,
contract execution, effect hardening, and teardown. This report does not claim
a live-room result or an official FDB-v3 score.

## Implementation audit

### Provider and LiveKit edge

- Added `NativeAudioProvider`, an implementation-independent construction
  boundary, plus the selected `GeminiNativeAudioProvider`.
- Configured Gemini Live input and output audio transcription where exposed by
  the official plugin.
- Added the FDB `AgentServer`/`AgentSession` worker and environment-only CLI
  preflight. No credential values are read into reports or printed.
- Locked LiveKit Agents to 1.3.x and the Google plugin to its compatible 1.3.x
  line. This prevents the `LanguageCode` import failure reproduced when a newer
  Google plugin is combined with LiveKit Agents 1.3.
- Added a pinned-source benchmark wrapper that delegates to the unmodified
  upstream batch script with provider `gemini2_5`.

### Transcript and interruption boundary

- LiveKit partial transcripts replace candidate text; final transcripts commit
  it. An EOT/listening transition is also a valid commit boundary when a
  provider does not expose transcript text.
- The bridge itself blocks every tool request while the current turn is still
  candidate-only. Prompt instructions are not treated as the safety boundary.
- A new user-speaking transition requests cancellation only for tools whose
  manifests declare them cancellable. FDB mutation tools remain non-cancellable.

### Tools, lifecycle, and effect reconciliation

- All twelve LiveKit tools are generated from the canonical manifests as raw
  JSON Schema tools; no wrapper signature can drift from the benchmark schema.
- Arguments are validated before dispatch and results are validated against
  strict Draft 2020-12 output schemas before use.
- Tool calls use the required lifecycle states and preserve the LiveKit call ID
  for the first attempt. Read retries retain one operation identity and receive
  fresh attempt IDs.
- Mutations receive deterministic room/tool/argument effect IDs, atomic
  per-effect deduplication, and tool-specific postcondition checks.
- A mutation timeout invokes backend verification. If no committed effect can
  be proven, status remains `UNKNOWN` and the bridge does not blindly retry.
- Duplicate model calls remain in official telemetry so the official evaluator
  can penalize them; only the backend effect is deduplicated.
- Shutdown closes new dispatch, cancels safe reads, drains accepted mutations,
  and marks a drain timeout `ABANDONED` without claiming completion.

### Official benchmark compatibility

- The asynchronous backend preserves the released mock values and latency
  profile ranges while avoiding blocking sleeps.
- It accepts all argument combinations observed in the released 100 scenarios,
  including `pets_allowed` and product `category` fields omitted by some
  upstream Python wrapper signatures.
- Apartment/product outputs include the paths used by released
  `$RESULT_n...` expressions while retaining upstream fields where applicable.
- Recursive nested result resolution supports object fields and array indexes.
- The official tool log has the exact structure consumed by
  `run_tool_benchmark.py`: `{room, call: {function, args, timestamp_start,
  timestamp_end}}`.
- The heartbeat emits the upstream `LATENCY_TRACK_JSON` prefix and fields.
- Rich transcript, lifecycle, error, latency, and shutdown telemetry is kept in
  a separate JSONL stream.

## Verification

| Check | Result |
|---|---|
| Full pytest suite | **279 collected, 279 passed** |
| Ruff (`src tests scripts`) | **Passed** |
| mypy (`src`) | **Passed — 46 source files** |
| `uv lock --check` | **Passed** |
| `uv sync --frozen --extra dev --extra fdb` | **Passed** |
| `git diff --check` | **Passed** |
| FDB optional import and Gemini model construction (placeholder key; no network) | **Passed** |
| Installed `continuum-fdb-agent --check` entrypoint | **Passed; no values displayed** |
| Installed `continuum-fdb-contract` entrypoint | **Passed** |
| Benchmark wrapper syntax/fail-fast check | **Passed** |
| Pinned upstream revision/source contract | **Passed** |
| Released scenarios/calls/tools/rollback cases | **100 / 154 / 12 / 21** |
| Internal bridge call-chain execution | **100 scenarios, 154/154 calls, 0 failures** |
| GitHub CI | **Pending publication run** |

Machine-readable evidence:

- `reports/phase02_fdb_source_contract.json`
- `reports/phase02_fdb_mock_contract.json`

The second report contains `kind: internal_contract_check` and
`official_score: false` by design.

## Adversarial and unsupported-claim review

| Risk | Evidence/control | Outcome |
|---|---|---|
| Partial ASR triggers an effect | Bridge commit gate plus candidate-only rejection test | Blocked |
| Wrong/missing argument reaches backend | Draft 2020-12 validation; dispatch log remains empty | Blocked |
| Malformed tool output is announced | Strict result schema before completion | Blocked |
| Successful response does not prove requested mutation | Concrete per-tool postcondition reconciliation | Blocked |
| Timeout causes duplicate mutation | Verify by stable effect ID; unknown outcome is not resent | Blocked |
| Concurrent duplicate mutation lands twice | Bridge/backend per-effect locks and cache | Blocked |
| Duplicate model call is hidden from evaluator | Every successful invocation is written to official log | Blocked |
| Barge-in cancels an accepted mutation | Only cancellable reads receive task cancellation | Blocked |
| Worker exit leaks tasks or claims false success | Read cancellation, mutation drain, abandoned timeout | Blocked |
| Wrapper/result paths cannot execute hard chains | All 154 released annotated calls execute successfully | Resolved |
| Provider swap requires core/bridge edits | `NativeAudioProvider` boundary; bridge has no provider import | Resolved |
| Credential value reaches Git or report | Names-only preflight/errors; values never logged | No value found |
| Internal contract pass is presented as official FDB quality | Explicit non-official marker in code, report, and docs | Blocked |

## External gates and residual limitations

The following were **not** executed and are not claimed:

1. A connected LiveKit Cloud room, because `LIVEKIT_URL`, `LIVEKIT_API_KEY`, and
   `LIVEKIT_API_SECRET` are absent.
2. A Gemini service call, because `GOOGLE_API_KEY` is absent. Local model
   construction confirms package/API compatibility only.
3. Audio conversion or released-WAV inference, because FFmpeg and the released
   FDB-v3 audio directory are absent.
4. The unmodified official tool accuracy, pass-rate, response, or latency
   evaluators. Therefore there is no official score in this milestone.
5. Live validation of provider event ordering, VAD/EOT behavior, spoken audio
   quality, timestamps/confidence, or the visual path. Those are Phase 3 gates.
6. Model-specific accuracy on correction, conditional, and repeated-tool speech.
   The local 154-call run validates bridge execution of annotations, not Gemini
   planning quality.

## Audit conclusion

The credential-free Phase 2 code gate is satisfied: the selected Gemini native
implementation remains replaceable, the FDB-managed LiveKit edge imports, all
released call chains execute through lifecycle/effect controls, telemetry
matches the upstream runner, and local quality gates pass. Milestone completion
still requires a published CI pass; live media and official benchmark quality
remain explicitly deferred to later milestones.
