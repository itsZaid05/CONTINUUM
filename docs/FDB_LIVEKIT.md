# FDB-v3 LiveKit edge

CONTINUUM's FDB edge follows the room workflow in the pinned
[`DanielLin94144/Full-Duplex-Bench`](https://github.com/DanielLin94144/Full-Duplex-Bench)
revision `3e799c45a045256f47d5f1c9cda90157e2d2ec9e`. Gemini native audio is the
selected implementation, behind a provider interface so the bridge and runtime
are not tied to Google.

## Architecture

```text
FDB released WAV -> upstream headless LiveKit client -> LiveKit room
                                                    -> FdbVoiceAgent
Gemini Live native audio <-> AgentSession <-> 12 manifest-generated tools
                                      |        -> FdbToolBridge
                                      |           |- argument/result schemas
                                      |           |- lifecycle + cancellation
                                      |           |- stable mutation effect IDs
                                      |           |- postcondition reconciliation
                                      |           `- FdbMockBackend
                                      `-> transcript/state/latency adapter
```

The LiveKit module is optional. Importing `continuum.integrations.fdb` does not
import LiveKit or read credentials. `NativeAudioProvider` is the replacement
boundary; `GeminiNativeAudioProvider` is the configured implementation.

## Reproducible install

```bash
uv sync --frozen --extra dev --extra fdb
# equivalent unlocked development install:
# python -m pip install -e '.[dev,fdb]'
```

The lock pins LiveKit Agents **1.3.11**, Google **1.3.12**, and compatible
OpenAI/Silero **1.3.11** plugins. Pinning the Google plugin explicitly avoids a
cross-minor import mismatch while retaining the official FDB `~1.3` API line.
The FDB extra also pins the OpenTelemetry 1.39.1 family: newer releases remove
the `LogData` import still used by this LiveKit Agents release.

## Environment

Supply values through the process environment. Do not put them in Git, command
history, reports, or chat.

```text
LIVEKIT_URL
LIVEKIT_API_KEY
LIVEKIT_API_SECRET
GOOGLE_API_KEY
```

Optional non-secret settings:

```text
CONTINUUM_MEDIA_PROVIDER=gemini_native_audio
GEMINI_LIVE_MODEL=gemini-2.5-flash-native-audio-preview-12-2025
GOOGLE_VOICE=Puck
GEMINI_LIVE_LANGUAGE=
FDB_LATENCY_PROFILE=instant
CONTINUUM_TELEMETRY_PATH=/tmp/continuum_fdb_telemetry.jsonl
FDB_TOOL_LOG_PATH=/tmp/agent_tool_calls.log
CONTINUUM_FDB_SHUTDOWN_TIMEOUT_S=5
```

The model default matches the pinned upstream Gemini 2.5 agent. Override it by
environment variable if Google retires that preview identifier; no core or tool
bridge code changes are required.

## Start the FDB-managed worker

```bash
continuum-fdb-agent --check start
continuum-fdb-agent start
# development worker:
continuum-fdb-agent dev
# official mock latency profile:
continuum-fdb-agent --latency normal start
```

The worker intentionally uses **automatic LiveKit dispatch** (it has no named
`agent_name`). This is required because the pinned, unmodified upstream
`livekit_inference.py` only creates/joins a new room and never sends a named
agent-dispatch request. Use a dedicated LiveKit project for this benchmark:
automatic dispatch joins the worker to every new room in that project. The
Phase 3 media smoke likewise creates one isolated room and relies on that same
automatic dispatch, rather than creating a duplicate named session.

### One-command official reproduction

With the released data directory and the five environment variables below,
this is the single submission command:

```bash
export LIVEKIT_URL=... LIVEKIT_API_KEY=... LIVEKIT_API_SECRET=...
export GOOGLE_API_KEY=... OPENAI_API_KEY=...  # OpenAI key is the upstream LLM judge
scripts/reproduce_fdb_v3.sh /path/to/fdb_v3_data_released
```

It uses `uv sync --frozen --extra dev --extra fdb --extra fdb-eval`, fetches and
audits the pinned upstream source, starts the CONTINUUM worker, runs every
recording through the **unmodified** upstream inference script, then invokes the
upstream tool-accuracy, strict-pass-rate, and latency evaluators with `--use-llm`.
Machine-readable reports, worker log, telemetry, and official-shaped tool log
are written beneath `reports/fdb-v3/<UTC-run-id>/`. Pass `--force` to overwrite
existing `result_gemini2_5.json` files; `--skip-sync` is useful only after the
locked environment has already been installed.

For a manually managed worker, the lower-level runner remains available:

```bash
scripts/run_fdb_benchmark.sh /path/to/fdb_v3_data_released
```

The upstream runner reads `/tmp/agent_tool_calls.log` using the exact official
`{room, call: {function, args, timestamp_start, timestamp_end}}` shape. It also
reads `LATENCY_TRACK_JSON` lines from `/tmp/agent_heartbeat.log`. Rich lifecycle,
transcript, error, and teardown events go to the separate CONTINUUM telemetry
JSONL file.

## Credential-free acceptance

Fetch the pinned source, audit its contract, then execute every released
annotated call chain against the bridge/backend:

```bash
scripts/fetch_fdb.sh
python scripts/audit_fdb_contract.py --source-dir .artifacts/Full-Duplex-Bench
continuum-fdb-contract \
  .artifacts/Full-Duplex-Bench/v3/benchmark_data_v2.json \
  --output reports/fdb_mock_contract.json
```

The contract runner checks all 100 scenarios and 154 ordered calls, including
nested `$RESULT_n.apartments[0].address` references and repeated same-tool
calls. It is explicitly labeled `official_score: false`: it validates plumbing,
not speech understanding or model quality.

## Safety and teardown invariants

- Partial transcript hypotheses are candidate state only. A final transcript or
  EOT boundary creates the committed utterance used for auditing.
- Concrete arguments are validated before the mock backend is touched.
- Backend results are validated against strict Draft 2020-12 schemas.
- Mutation success is accepted only after a tool-specific postcondition passes.
- A timed-out mutation is verified by stable effect ID and is never blindly
  resent while its outcome is unknown.
- User barge-in cancels declared-cancellable reads only. Accepted mutations are
  drained during shutdown and never reported as cancelled if they are not.
- Duplicate model calls remain visible to the official evaluator, while the
  backend effect is committed at most once.
- Worker shutdown cancels safe reads, waits for accepted mutations, and marks a
  drain timeout as abandoned without claiming completion.

## Phase 3 media gate

Phase 3 adds explicit Gemini server-VAD/transcription/image policy, explicit LiveKit
room audio/video/transcript I/O, normalized `continuum.media.v1` telemetry, and
atomic PCM16 provider-output recording. Run the credential-free preflight with:

```bash
continuum-fdb-media-smoke --allow-skip --output reports/phase03_live_smoke.json
```

That command records `skipped_external_gate`; it is not a live pass. A credentialed
run must omit `--allow-skip`. Requested word timestamps and confidence remain null
unless the provider exposes them. Provider-output and remotely received WAV files
have distinct provenance. See `reports/phase03_media_audit.md`.

## Honest boundary

The local contract suite does **not** establish an official FDB score. Official
metrics require the released 100-recording dataset, a connected LiveKit room,
Gemini service access, FFmpeg/ASR dependencies, and execution of the unmodified
upstream evaluators. Those outputs belong to the official evaluation milestone.
