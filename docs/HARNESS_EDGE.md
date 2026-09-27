# Organizer harness edge

CONTINUUM exposes the scored path through a JSONL stdio process:

```bash
continuum kit                       # external tools (organizer executes calls)
continuum kit --local-tools         # deterministic mock sandbox / smoke tests
python scripts/kit_smoke.py
```

Every non-empty stdin line is one JSON object. Every stdout line is one action
object and is flushed immediately. Diagnostics belong on stderr; the command
does not print banners on stdout.

## Canonical events

| Event | Required fields | Notes |
|---|---|---|
| `text` | `session_id`, `text` | Chunks are buffered until `eot`. |
| `eot` | `session_id` | Produces one fast acknowledgement, then plans. |
| `audio` | `session_id`, `data` and/or `text` | `data` is base64 WAV. `text` is an upstream transcript; `confidence < 0.72` clarifies without acting. |
| `frame` | `session_id`, `data` and/or `text` | `data` is base64 PNG. `text` is upstream OCR; low confidence clarifies. |
| `interrupt` | `session_id`, optional `call_id` | A named live call is cancelled. Plan-diff reconciliation handles a bare barge-in after the replacement turn arrives. |
| `tool_result` | `session_id`, `call_id`, `result` | Matched only to a live external call; stale/unknown results are ignored. |
| `manifest` | `session_id`, `manifest` or `manifests` | The first scenario manifest replaces built-ins for that session. |
| `warmup` | — | Returns `ready`; deterministic and network-free. |

Accepted kit aliases include `user_text`, `end_of_turn`, `audio_chunk`,
`video_frame`, `interruption`, `tool_response`, `tool_manifest`, `session`,
`sessionId`, `callId`, `toolCallId`, `input_schema`, and `output_schema`.
`data:` URIs and arrays of byte values are accepted as alternatives to plain
base64.

Example:

```jsonl
{"type":"tool_manifest","session":"demo","manifest":{"name":"lookup","description":"look up a manual","input_schema":{"type":"object","properties":{"model":{"type":"string"}},"required":["model"]},"output_schema":{"properties":{"answer":{"type":"string"}}},"read_only":true}}
{"type":"user_text","session":"demo","text":"look up the manual for WM-4500"}
{"type":"end_of_turn","session":"demo","timestamp_ms":20}
```

An external-mode response might be:

```json
{"ts_ms":4.21,"type":"tool_call","call_id":"…","tool":"lookup","args":{"model":"WM-4500"}}
```

Return its result with the same call id:

```json
{"type":"tool_response","session":"demo","toolCallId":"…","output":{"answer":"Check drain filter"}}
```

## Actions and invariants

The action union is `speak | tool_call | cancel | clarify | final`.

- Every action has a runtime `ts_ms`.
- Every tool call has a unique `call_id`; retries receive a fresh call id.
- A cancellation names an emitted call and carries a **fresh** state snapshot.
- A `final` contains `snapshot {intent, slots, version}` and is emitted only
  after an accepted tool result. `intent.grounded_by` names its tool and call.
- The 120-second watchdog emits a truthful `timed_out` final; it never claims
  an unconfirmed effect completed.
- Bad JSON, unsupported events, invalid base64, or invalid schemas produce a
  `clarify` action and do not terminate the process.

## Audio and frame grounding

The default build is offline:

1. Use organizer-provided transcript/OCR text when present.
2. WAV `ICMT`/`INAM` and PNG `tEXt` metadata are deterministic test hand-offs.
3. Audio with no transcript and frames with no OCR clarify instead of guessing.
4. `--asr-model /existing/model/directory` enables optional
   `faster-whisper`. The path must already exist and is loaded with
   `local_files_only=True`; no model is downloaded.

The checked-in multimodal suite uses upstream transcript/OCR evidence and
explicit confidence values, so `make eval-b` is reproducible without media
models or API keys.

## Programmatic use

```python
from continuum.harness_edge import JsonlBridge, normalize_event
from continuum.runtime import AgentRuntime

edge = JsonlBridge(AgentRuntime(tool_mode="external"), watchdog_s=120)
event = normalize_event({"type": "user_text", "session": "s", "text": "hello"})
```

`JsonlBridge.process_line()` and `iter_jsonl()` are convenient for tests.
`AgentRuntime.input_queue` / `output_queue` remain available for direct async
integration.
