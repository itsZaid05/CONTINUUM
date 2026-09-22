# Arbiter Accuracy — offline-fake (gold b8920267657a)

- **Accuracy:** 100.00% (100/100)
- **Macro-F1:** 1.000
- **Latency:** p50 15.0ms, p95 24ms
- **ECE:** 0.106

| Category | Precision | Recall | F1 | Support |
|---|---:|---:|---:|---:|
| NEW_GOAL | 1.000 | 1.000 | 1.000 | 20 |
| MODIFY | 1.000 | 1.000 | 1.000 | 20 |
| ADD_CONSTRAINT | 1.000 | 1.000 | 1.000 | 20 |
| RETRACT | 1.000 | 1.000 | 1.000 | 20 |
| NOISE | 1.000 | 1.000 | 1.000 | 20 |

## Confusion

| gold \ pred | NEW_GOAL | MODIFY | ADD_CONSTRAINT | RETRACT | NOISE |
|---|---|---|---|---|---|
| NEW_GOAL | 20 | 0 | 0 | 0 | 0 |
| MODIFY | 0 | 20 | 0 | 0 | 0 |
| ADD_CONSTRAINT | 0 | 0 | 20 | 0 | 0 |
| RETRACT | 0 | 0 | 0 | 20 | 0 |
| NOISE | 0 | 0 | 0 | 0 | 20 |