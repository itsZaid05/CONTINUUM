# Raw-media evaluation — real ASR and OCR inside the runtime

Recognizers: ASR `models\faster-whisper-base.en` (faster-whisper, local), OCR `rapidocr`. Fixtures carry no transcript or OCR text; pivot latency includes real recognition time.

Suite `data/runtime_scenarios/raw_media_suite.json` (7 raw-media scenarios), time scale 1.0 (event times and tool delays scaled together; latencies are real in-process ms). Category weights follow the Theme 05 guide (40 / 35 / 15 / 10); the scorer is our approximation — the official one is not released.

## Systems

| System | Score | Task | Interrupt | Latency | Safety | Stale-action rate | Stale reruns | Dup. mutations | Regretted irreversible | Unneeded clarify | Tool calls | Pivot p50 / p95 ms | Ack p95 ms | Cancel p95 ms |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| continuum | **100.0** | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 | 0 | 0 | 0 | 0.0 | 7 | 894.388 / 894.388 | 0.027 | 894.102 |

`continuum` = generic planner + reconcile/advance executor. `naive_runtime` = the runtime path before this work (first read-only tool, args copied from arbiter state, cancel everything on any change). Rows below the three systems are single-mechanism ablations of `continuum`.

## Per scenario (continuum vs naive runtime)

| Scenario | Theme | continuum | naive | What continuum did not get right |
|---|---|---|---|---|
| raw_audio_flight_pivot | raw_audio_interruption | 100.0 | — | — |
| raw_audio_misheard_city | raw_audio_safety | 100.0 | — | — |
| raw_audio_ticket_clarify | raw_audio_clarification | 100.0 | — | — |
| raw_frame_manual_lookup | raw_vision | 100.0 | — | — |
| raw_crossmodal_repair_confirm | raw_crossmodal | 100.0 | — | — |
| raw_frame_no_text | raw_vision_safety | 100.0 | — | — |
| raw_frame_blurred | raw_vision_safety | 100.0 | — | — |
