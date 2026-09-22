# Baseline vs CONTINUUM

Backend: `offline-fake` — baseline column is the naive **redo-all agent** (`src/continuum/baseline.py`: 2 LLM calls/turn, no versioning, no stale gate, no ledger verify), CONTINUUM column is the full pipeline.

| Scenario | Baseline agent | CONTINUUM | Saved | Baseline defects | C✓ | DB | Shadows | Reused% | Wasted% |
|---|---:|---:|---:|---|:-:|:-:|---:|---:|---:|
| delhi_bangalore | 6480ms | 1300ms | 79.9% | stale applied | ✓ | — | 0 | 0.0% | 0.0% |
| dont_book_it | 4320ms | 1100ms | 74.5% | stale applied, retract ignored → booked | ✓ | — | 0 | 0.0% | 0.0% |
| duplicate_result | 2160ms | 1050ms | 51.4% | — | ✓ | — | 0 | 0.0% | 0.0% |
| rapid_burst | 6480ms | 1350ms | 79.2% | — | ✓ | — | 0 | 0.0% | 0.0% |
| retract_after_commit | 4320ms | 2400ms | 44.4% | stale applied, retract ignored → booked, fake undo claim | ✓ | — | 0 | 0.0% | 0.0% |
| shadow_bangalore | 4320ms | 1100ms | 74.5% | — | ✓ | — | 2 | 50.0% | 50.0% |
| timeout_booking | 2160ms | 2250ms | 0.0% | blind retry → double-book | ✓ | ✗ | 0 | 0.0% | 0.0% |

C✓ = CONTINUUM correctness check (zero stale leaks, honest retract); DB = baseline double-book. Baseline analytic redo-all wall kept in JSON (`baseline_analytic_ms`) for transparency.

Generated at 2026-09-22T03:25:30.196113