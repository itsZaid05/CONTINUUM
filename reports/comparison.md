# Baseline vs CONTINUUM

Backend: `offline-fake`

| Scenario | Baseline | CONTINUUM | Saved | Dispatched | Invalidated | Reused |
|---|---:|---:|---:|---:|---:|---:|
| delhi_bangalore | 2450ms | 1300ms | 46.9% | 3 | 1 | 2 |
| dont_book_it | 2400ms | 1100ms | 54.2% | 2 | 1 | 1 |
| rapid_burst | 1350ms | 1350ms | 0.0% | 1 | 0 | 1 |
| retract_after_commit | 2400ms | 1100ms | 54.2% | 2 | 2 | 0 |
| shadow_bangalore | 2100ms | 1100ms | 47.6% | 2 | 1 | 1 |
| timeout_booking | 2250ms | 2250ms | 0.0% | 2 | 0 | 2 |

Generated at 2026-09-22T00:07:20.095721