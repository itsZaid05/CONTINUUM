# Runtime evaluation — timed scenarios through `AgentRuntime`

Suite `data/runtime_scenarios/text_suite.json` (18 text scenarios), time scale 0.1 (event times and tool delays scaled together; latencies are real in-process ms). Category weights follow the Theme 05 guide (40 / 35 / 15 / 10); the scorer is our approximation — the official one is not released.

## Systems

| System | Score | Task | Interrupt | Latency | Safety | Stale-action rate | Stale reruns | Dup. mutations | Regretted irreversible | Unneeded clarify | Tool calls | Pivot p50 / p95 ms | Ack p95 ms | Cancel p95 ms |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| continuum | **100.0** | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 | 0 | 0 | 0 | 0.0 | 35 | 1.091 / 3.409 | 0.1 | 1.759 |
| continuum+speculation | **100.0** | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 | 0 | 0 | 0 | 0.0 | 34 | 1.131 / 1.888 | 0.099 | 1.702 |
| naive_runtime | **54.65** | 0.1296 | 0.6991 | 1.0 | 1.0 | 0.0 | 0 | 0 | 0 | 0.2222 | 22 | 0.257 / 0.582 | 0.111 | None |
| no_verify_after_timeout | **99.72** | 1.0 | 1.0 | 1.0 | 0.9722 | 0.0 | 0 | 1 | 0 | 0.0 | 36 | 0.765 / 1.021 | 0.103 | 0.878 |
| no_selective_cancel | **98.7** | 1.0 | 0.963 | 1.0 | 1.0 | 0.0 | 0 | 0 | 0 | 0.0 | 37 | 1.108 / 1.509 | 0.123 | 1.359 |
| no_read_retry | **97.78** | 0.9444 | 1.0 | 1.0 | 1.0 | 0.0 | 0 | 0 | 0 | 0.0 | 33 | 0.919 / 1.537 | 0.115 | 1.338 |
| no_commit_gate | **96.67** | 0.9167 | 1.0 | 1.0 | 1.0 | 0.0 | 0 | 0 | 1 | 0.0 | 36 | 0.969 / 1.448 | 0.115 | 0.972 |

`continuum` = generic planner + reconcile/advance executor. `naive_runtime` = the runtime path before this work (first read-only tool, args copied from arbiter state, cancel everything on any change). Rows below the three systems are single-mechanism ablations of `continuum`.

## Per scenario (continuum vs naive runtime)

| Scenario | Theme | continuum | naive | What continuum did not get right |
|---|---|---|---|---|
| flight_pivot | interruption | 100.0 | 36.67 | — |
| rapid_corrections | interruption | 100.0 | 33.75 | — |
| backchannel_mid_task | interruption | 100.0 | 36.67 | — |
| additive_goal | interruption | 100.0 | 36.67 | — |
| retract_before_commit | retraction | 100.0 | 50.0 | — |
| retract_after_commit | retraction | 100.0 | 60.0 | — |
| booking_timeout_committed | fault | 100.0 | 60.0 | — |
| booking_timeout_not_committed | fault | 100.0 | 60.0 | — |
| read_retry | fault | 100.0 | 60.0 | — |
| ticket_unseen_tools | unseen_tools | 100.0 | 60.0 | — |
| ticket_correction_inflight | interruption | 100.0 | 36.67 | — |
| clarify_missing_slot | clarification | 100.0 | 80.0 | — |
| manual_lookup | unseen_tools | 100.0 | 60.0 | — |
| irreversible_confirmed | clarification | 100.0 | 80.0 | — |
| irreversible_declined | clarification | 100.0 | 100.0 | — |
| nav_pivot | interruption | 100.0 | 36.67 | — |
| climate_correction | interruption | 100.0 | 36.67 | — |
| dining_chain_unseen | unseen_tools | 100.0 | 60.0 | — |

## Ablations — what each mechanism buys

| Ablation | Score (Δ vs continuum) | Scenarios that got worse |
|---|---|---|
| no_verify_after_timeout | 99.72 (-0.28) | booking_timeout_committed (100.0→95.0: effect cap exceeded: ['hold_seat', 'confirm_booking', 'confirm_booking']) |
| no_selective_cancel | 98.7 (-1.30) | additive_goal (100.0→88.33: kept work was cancelled or restarted: {'tool': 'search_flights', 'args': {'to': 'Goa'}} (2 dispatches))<br>retract_before_commit (100.0→88.33: kept work was cancelled or restarted: {'tool': 'search_flights', 'args': {'to': 'Pune'}} (2 dispatches)) |
| no_read_retry | 97.78 (-2.22) | read_retry (100.0→60.0: not completed: {'tool': 'search_flights', 'args': {'to': 'Chennai', 'date': '2026-09-30'}}; final slots {} vs {'to': 'Chennai'}) |
| no_commit_gate | 96.67 (-3.33) | irreversible_confirmed (100.0→80.0: expected a clarification)<br>irreversible_declined (100.0→60.0: forbidden completion: ['schedule_technician']; expected a clarification) |

## Speculation (read-only shadows)

Spawned 18, promoted 1 (reuse rate 0.056), discarded 17; tool latency hidden by promoted shadows 31.71 ms (scaled clock), shadow tool time wasted 1551.97 ms. Shadows are local and never emitted as harness tool calls, so they do not change the scored action stream; enable them only where the extra backend load is acceptable.
