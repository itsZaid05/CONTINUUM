# Blueprint implementation and gate ledger

**Started:** 27 Sep 2026
**Active branch:** `arena/01a0deea-continuum`
**Baseline:** `dcb90fa68a2af69d39cb290bf0f3a4916502161d`

This is the execution ledger for closing every gap found in the final
Top-1%-oriented blueprint audit. A phase may be marked complete only after its
code, tests, lint, types, documentation, and phase-specific acceptance checks
all pass. Internal deterministic regressions are never reported as official
FDB-v3 measurements.

## External source of truth

- Repository: <https://github.com/DanielLin94144/Full-Duplex-Bench>
- Audited revision: `3e799c45a045256f47d5f1c9cda90157e2d2ec9e`
- Upstream license: CC BY-NC 4.0 (do not vendor or redistribute benchmark data)
- Released FDB-v3 schema: `v3/benchmark_data_v2.json`
- Released data: 100 recordings, 12 speakers, four domains, twelve tools
- Official runtime: LiveKit Agents `~1.3`; selected path is Gemini native audio
  through the official Google realtime plugin (provider interface retained)
- Official reports: tool F1/argument/response accuracy, strict pass rate, and
  latency analysis

`scripts/fetch_fdb.sh` fetches and verifies the exact source revision into the
ignored `.artifacts/` directory.

## Phase plan

| Phase | Scope | Exit gate | State |
|---|---|---|---|
| 0 | Baseline alignment, official-source pin, implementation ledger, dependency lock | Branch aligned; upstream revision/license recorded; baseline checks reproducible | Complete — 27 Sep 2026 |
| 1 | Runtime foundation: candidate/committed speech, exact call lifecycle, JSON Schema dispatch validation, FDB contracts/manifests | Unit/contract tests plus existing 246 regressions; illegal transitions and partial-speech side effects tested | Complete — 27 Sep 2026 |
| 2 | FDB/LiveKit edge: Gemini provider interface/implementation, agent, transcript adapter, tool bridge, telemetry, official-shape mocks | Optional FDB install imports; 12 tools exposed; 100/154 call-chain check; schemas, postconditions, cancellation, teardown, and official logs pass | Implemented; local gate passed — audit/CI pending |
| 3 | Official media path: Gemini Live native audio, provider transcript/audio events, VAD/EOT/barge-in, timestamps/confidence/visual path where exposed | Live room smoke test with environment-only credentials; recorded WAV and call telemetry | Not started |
| 4 | Full official evaluation: fetch 100 recordings, batch runner, official scorers, repeatability | 100/100 processed; official reports archived with provenance; no synthetic score substituted | Blocked by credentials/data download |
| 5 | Additional state/effect stress testing beyond the Phase 2 bridge hardening | Fault, duplicate, stale, out-of-order, cleanup, and FDB hard-chain tests pass | Core bridge requirements merged into Phase 2 |
| 6 | Extension through identical stack | In-car extension runs through LiveKit audio/tool/TTS path with separate results | Not started |
| 7 | Submission package: lock, clean-clone/public scripts, root docs, results, architecture, demo, slides/video | Clean-clone smoke passes; every §22 artifact present and linked | Not started |

## Phase 0/1 acceptance inventory

- [x] Official FDB source URL and immutable revision identified.
- [x] Official benchmark contracts represented without importing LiveKit.
- [x] All twelve official tool names and observed argument fields represented.
- [x] Dedicated lifecycle states added: `CREATED`, `RUNNING`,
  `CANCEL_REQUESTED`, `CANCELLED`, `ABANDONED`, `COMPLETED`, `UNKNOWN`.
- [x] Non-cancellable invalidation becomes `ABANDONED`, not fake `CANCELLED`.
- [x] Streaming candidate text is separate from committed utterances.
- [x] Candidate transcript updates cannot dispatch tools.
- [x] Full JSON Schema validation is applied at manifest and dispatch boundaries.
- [x] Existing and new tests pass locally (261 on Python 3.11); Python 3.10-3.12 CI is configured.
- [x] Ruff and mypy pass.
- [x] `uv.lock` generated; `uv lock --check` and `uv sync --frozen --extra dev` pass.
- [x] Phase audit committed and pushed; Python 3.10-3.12 CI passed in run `36296693342`.

## Phase 2 acceptance inventory

- [x] Provider-neutral `NativeAudioProvider` boundary with Gemini native audio selected.
- [x] Compatible LiveKit Agents/Google plugin line locked; import/model construction smoke passes.
- [x] FDB-managed room agent and environment-only CLI entrypoint implemented.
- [x] All twelve tools generated from canonical raw JSON Schemas.
- [x] Partial/final/EOT transcript mapping and barge-in cancellation adapter implemented.
- [x] Strict argument and result validation enforced around every bridge call.
- [x] Mutation effect deduplication, timeout verification, and concrete postconditions enforced.
- [x] Cancellable reads stop on barge-in; accepted mutations drain during teardown.
- [x] Upstream-compatible tool and latency telemetry shapes implemented.
- [x] Nested result references and repeated same-tool calls supported.
- [x] Internal contract run passes all 100 scenarios and 154 calls; labeled non-official.
- [x] 279 tests, Ruff, mypy, lock check, optional FDB sync, and source import smoke pass locally.
- [ ] GitHub CI and phase audit publication (required before milestone approval).

## Known external gates

The current execution environment has no configured `LIVEKIT_*` or
`GOOGLE_API_KEY` variables, the released FDB recordings are not mounted, and
`ffmpeg` is not installed. The official FDB-v3 README requires a LiveKit Cloud
account even though its scripts create rooms automatically. Values must be
supplied only as environment variables—never committed or pasted into logs.
These do not block Phase 2 contract work, but they block the live media smoke
test and full benchmark in Phases 3-4.
