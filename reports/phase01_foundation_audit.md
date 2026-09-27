# Phase 0-1 audit — FDB source pin and runtime foundation

**Date:** 27 Sep 2026
**Baseline:** `dcb90fa68a2af69d39cb290bf0f3a4916502161d`
**Scope:** Blueprint Phases 0 and 1 only. No LiveKit inference or official FDB
score is claimed by this report.

## Implemented

1. Pinned and audited upstream Full-Duplex-Bench revision
   `3e799c45a045256f47d5f1c9cda90157e2d2ec9e`.
2. Added a non-vendoring source fetcher and contract-drift audit.
3. Added validated FDB benchmark/scenario/tool-call contracts.
4. Added manifests for all twelve released FDB-v3 tools, including argument
   fields observed in the official 100-scenario JSON.
5. Added explicit logical call lifecycle states:
   `CREATED`, `RUNNING`, `CANCEL_REQUESTED`, `CANCELLED`, `ABANDONED`,
   `COMPLETED`, and `UNKNOWN`.
6. Added stable logical operation IDs, per-attempt call IDs, base versions,
   effect keys, external operation IDs, transition logs, and runtime
   introspection.
7. Made non-cancellable invalidated work `ABANDONED`; its late result is
   rejected and its uncertain effect is not blindly re-dispatched.
8. Added separate streaming candidate and committed utterance state. Partial
   replacement hypotheses cannot reach the planner or dispatch tools.
9. Added JSON Schema meta-validation at manifest creation and concrete argument
   validation before dispatch.
10. Added and committed a universal `uv.lock` covering default and optional
    dependency groups.

## Verification

| Check | Result |
|---|---|
| Existing + new pytest suite | **261 passed** |
| Ruff (`src tests scripts`) | **Passed** |
| mypy (`src`) | **Passed — 36 source files** |
| `uv lock --check` | **Passed** |
| `uv sync --frozen --extra dev` | **Passed** |
| `git diff --check` | **Passed** |
| Pinned FDB source checkout | **Passed** |
| Official benchmark contract parse | **100 scenarios, 154 expected calls** |
| Official tool-set parity | **12/12 exact names** |
| Official rollback count | **21** |

## Tests added

- `tests/test_lifecycle.py`
  - legal/illegal transitions;
  - retries under one operation;
  - timeout `UNKNOWN` reconciliation;
  - cancellable `CANCEL_REQUESTED → CANCELLED`;
  - non-cancellable `RUNNING → ABANDONED` and late-result rejection.
- `tests/test_streaming.py`
  - replacement and append transcript modes;
  - no side effects from partial hypotheses;
  - exactly one commit when a final transcript is followed by EOT.
- `tests/test_fdb_contracts.py`
  - benchmark integrity;
  - ordered multi-tool calls;
  - all twelve manifests and safety metadata.
- `tests/test_manifest_registry.py`
  - concrete argument type validation;
  - invalid JSON Schema rejection.

## Explicitly not verified in this phase

- LiveKit imports or a connected room.
- Official Silero/OpenAI STT, gpt-4o, or OpenAI TTS.
- Any of the 100 released WAV recordings.
- Official FDB-v3 scorer output or latency.
- Provider credentials or `ffmpeg` availability.

Those are Phase 2-4 gates and remain open. The execution environment currently
has no `LIVEKIT_*`/`OPENAI_API_KEY` variables and no `ffmpeg` binary.
