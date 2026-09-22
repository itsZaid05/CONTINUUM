# Shadow (Speculation) Metrics — Phase 4

| Scenario | Spawned | Promoted (reused) | Discarded+Abandoned (wasted) | Reused % | Wasted % | Cleanup p95 | Primary slowdown |
|---|---:|---:|---:|---:|---:|---:|---:|
| shadow_bangalore | 2 | 1 | 1 | 50.0% | 50.0% | 0.009ms | 4.5% |

**Totals:** 2 spawned → 1 reused (50.0%), 1 wasted (50.0%).

Readout: speculation pays only when it is cheap and harmless — wasted shadows are cleaned <150ms, the primary never loses >5% of its wall time, and book/pay risks are never speculated at all (READ/STAGE only). Scenarios outside the [0.55, 0.72) confidence band spawn zero shadows: the budget is never wasted on confident turns.