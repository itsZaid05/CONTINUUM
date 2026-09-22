# Baseline vs CONTINUUM

Backend: `offline-fake`

| Scenario | Baseline | CONTINUUM | Saved | Dispatched | Invalidated | Reused | Shadows | Reused% | Wasted% |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| delhi_bangalore | 2450ms | 1300ms | 46.9% | 3 | 1 | 2 | 0 | 0.0% | 0.0% |
| dont_book_it | 2400ms | 1100ms | 54.2% | 2 | 1 | 1 | 0 | 0.0% | 0.0% |
| rapid_burst | 1350ms | 1350ms | 0.0% | 1 | 0 | 1 | 0 | 0.0% | 0.0% |
| retract_after_commit | 2400ms | 2400ms | 0.0% | 2 | 0 | 2 | 0 | 0.0% | 0.0% |
| shadow_bangalore | 2100ms | 1100ms | 47.6% | 2 | 1 | 1 | 2 | 50.0% | 50.0% |
| timeout_booking | 2250ms | 2250ms | 0.0% | 2 | 0 | 2 | 0 | 0.0% | 0.0% |

Generated at 2026-09-22T03:09:47.666035