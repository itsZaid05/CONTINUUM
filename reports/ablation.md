# Ablation — what each mechanism buys

| Mechanism | With | Without | Verdict |
|---|---|---|---|
| stale-result gate | `{"stale_leaks": 0, "saved_pct": 46.9}` | `{"stale_leaks": 2, "saved_pct": 0.0}` | without it, stale Delhi results are applied to Bangalore state (2 leak(s)) — correctness bug |
| shadow speculation | `{"spawned": 2, "promoted": 1, "discarded": 1, "abandoned": 0, "reused_pct": 50.0, "wasted_pct": 50.0, "cleanup_p95_ms": ` | `{"spawned": 0, "promoted": 0, "discarded": 0, "abandoned": 0, "reused_pct": 0.0, "wasted_pct": 0.0, "cleanup_p95_ms": 0.` | 50.0% of shadow work reused; cost capped (<=4.5% primary slowdown, cleanup 0.016ms p95) |
