# Arbiter Accuracy — dense (gold b8920267657a)

- **Accuracy:** 98.00% (98/100)
- **Macro-F1:** 0.980
- **Latency:** p50 12.0ms, p95 16ms
- **ECE:** 0.247

| Category | Precision | Recall | F1 | Support |
|---|---:|---:|---:|---:|
| NEW_GOAL | 1.000 | 0.950 | 0.974 | 20 |
| MODIFY | 0.909 | 1.000 | 0.952 | 20 |
| ADD_CONSTRAINT | 1.000 | 1.000 | 1.000 | 20 |
| RETRACT | 1.000 | 0.950 | 0.974 | 20 |
| NOISE | 1.000 | 1.000 | 1.000 | 20 |

## Confusion

| gold \ pred | NEW_GOAL | MODIFY | ADD_CONSTRAINT | RETRACT | NOISE |
|---|---|---|---|---|---|
| NEW_GOAL | 19 | 1 | 0 | 0 | 0 |
| MODIFY | 0 | 20 | 0 | 0 | 0 |
| ADD_CONSTRAINT | 0 | 0 | 20 | 0 | 0 |
| RETRACT | 0 | 1 | 0 | 19 | 0 |
| NOISE | 0 | 0 | 0 | 0 | 20 |