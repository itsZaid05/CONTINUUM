# Multimodal runtime evaluation — audio and frame scenarios

Suite `data/runtime_scenarios/multimodal_suite.json` (11 multimodal scenarios), time scale 0.1 (event times and tool delays scaled together; latencies are real in-process ms). Category weights follow the Theme 05 guide (40 / 35 / 15 / 10); the scorer is our approximation — the official one is not released.

## Systems

| System | Score | Task | Interrupt | Latency | Safety | Stale-action rate | Stale reruns | Dup. mutations | Regretted irreversible | Unneeded clarify | Tool calls | Pivot p50 / p95 ms | Ack p95 ms | Cancel p95 ms |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| continuum | **100.0** | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 | 0 | 0 | 0 | 0.0 | 7 | 4.762 / 4.762 | 0.114 | 2.652 |
| naive_runtime | **81.15** | 0.5818 | 0.9394 | 1.0 | 1.0 | 0.0 | 0 | 0 | 0 | 0.2727 | 2 | 0.497 / 0.497 | 0.095 | None |

`continuum` = generic planner + reconcile/advance executor. `naive_runtime` = the runtime path before this work (first read-only tool, args copied from arbiter state, cancel everything on any change). Rows below the three systems are single-mechanism ablations of `continuum`.

## Per scenario (continuum vs naive runtime)

| Scenario | Theme | continuum | naive | What continuum did not get right |
|---|---|---|---|---|
| audio_flight_pivot | multimodal_interruption | 100.0 | 36.67 | — |
| frame_ticket_unseen_tools | multimodal_unseen_tools | 100.0 | 60.0 | — |
| audio_clarify_missing_slot | multimodal_clarification | 100.0 | 80.0 | — |
| frame_manual_lookup | multimodal_unseen_tools | 100.0 | 60.0 | — |
| audio_irreversible_confirmed | multimodal_clarification | 100.0 | 80.0 | — |
| low_confidence_audio_1 | multimodal_confidence | 100.0 | 100.0 | — |
| low_confidence_frame_2 | multimodal_confidence | 100.0 | 100.0 | — |
| low_confidence_audio_3 | multimodal_confidence | 100.0 | 100.0 | — |
| low_confidence_frame_4 | multimodal_confidence | 100.0 | 100.0 | — |
| low_confidence_audio_5 | multimodal_confidence | 100.0 | 100.0 | — |
| frame_manual_grounding_facets | multimodal_grounding | 100.0 | 76.0 | — |
