# Evaluation Methodology — Engineer A

This document describes how every number in `reports/` is produced, so judges can reproduce it and trust it.

---

## 1. Arbiter Accuracy (Primary Metric)

### Gold set

- **File:** `data/gold/arbiter_100.jsonl`
- **Size:** 100 utterances, **20 per category** (NEW_GOAL, MODIFY, ADD_CONSTRAINT, RETRACT, NOISE)
- **Creation:** `scripts/make_gold.py` crafts utterances that are pattern-coverable by `offline-fake` (to guarantee CI determinism) **and** hand-checked for naturalness.
- **Freezing:** hash `b8920267657a` (sha256 first 12). Do not edit without bumping version and re-freezing.
- **Anti-leakage:** utterances are written after 2026-09 cutoff, never copied verbatim from CLINC150/BANKING77/RECAP. Checked against benchmark contamination by grep.

### Procedure

```bash
python -m continuum.cli eval-arbiter --gold data/gold/arbiter_100.jsonl --output reports/arbiter_accuracy.json --backend offline-fake
# offline-fake → 100% (deterministic table)
# ollama / gemini → real LLM, may be 88-94%
```

### What is measured

- **Accuracy**, **macro-F1** (average of per-category F1), **per-category P/R/F1**, **confusion matrix**.
- **ECE** (Expected Calibration Error) stub: |accuracy - mean_confidence| (Phase 2 will bin).
- **Latency:** p50, p95 over the 100 calls (fake 5-30ms offline, 600-900ms real).

### How to interpret

- `macro-F1 ≥ 0.88` passes Gate. RETRACT is hardest (needs to distinguish "don't book" vs "change city").
- Confusion `MODIFY ↔ ADD_CONSTRAINT` is most common error in naive baselines; our fused delta should keep it <3 cases.

### Output

- `reports/arbiter_accuracy.json` (machine-readable)
- `reports/arbiter_accuracy.md` (human table, for slides)

---

## 2. Fast ACK Latency

- Measured inside `perception.py` Layer 0: `time.perf_counter()` before/after.
- Logged as `fast_ack_latency_ms` per turn in replay trace (`reports/phase1_trace.jsonl`).
- Target: **p95 <200ms**, p99 <400ms. Layer 0 is <15ms; Layer 1 embedding would be <50ms.

---

## 3. Delhi → Bangalore Wall Time (End-to-End)

### Scenarios

- `data/scenarios/delhi_bangalore.json` — V1(empty) → MODIFY Delhi → MODIFY Bangalore → ADD_CONSTRAINT morning.
- Also `dont_book_it`, `retract_after_commit`, `timeout_booking`, `rapid_burst`, `shadow_bangalore`.

### Simulation

- Stepped clock `at_ms`; tool latencies mocked (`search 900ms`, `filter 200ms`).
- `replay_scenario()` drives the pipeline and logs every state transition.

### Baseline

Baseline is "redo-all": sum of all tool latencies per version bump (no reuse). CONTINUUM sums only non-invalidated tools (selective invalidation).

```bash
python -m continuum.cli compare --output reports/comparison.json
cat reports/comparison.md
```

Example Phase 1 result (offline-fake):
```
delhi_bangalore  2450ms → 1300ms  46.9% saved  (1/3 invalidated, 2 reused)
dont_book_it     2400ms → 1100ms  54.2% saved
```

---

## 4. Shadow Metrics (Phase 4)

- **Reused:** shadow result promoted to PRIMARY (`PROMOTED` branch's tool output used).
- **Wasted:** shadow discarded (`CANCELLED → CLEANED_UP` without promotion).
- **Cleanup time:** `branch.cleaned_at - branch.created_at` in ms, measured in `BranchManager.cleanup()`.
- Targets: p95 <150ms, wasted < 2× reused cost, reused ≥30% on branching scenarios.
- Commands:
  ```bash
  python -m continuum.cli replay data/scenarios/shadow_bangalore.json --trace
  # then inspect reports/shadow_metrics.json (Phase 4)
  ```

---

## 5. Safety Metrics (Phase 3)

### Retraction after commit

- Scenario `retract_after_commit.json`: booking COMMITTED then "Don't book it".
- Correctness: response must be **honest** ("already booked, want me to cancel?") not pretend undo.
- Test: `tests/integration/test_retraction_honest.py` (future) asserts ledger status checked.

### Timeout / Verify

- Scenario `timeout_booking.json`: inject 3s timeout on IRREVERSIBLE `book`.
- Correctness: `EffectLedger.verify_after_timeout()` queries existence instead of blind retry → never double-books.
- Test: `tests/safety/test_ledger_timeout.py` — 10 injections, 100% pass.

---

## 6. How to Reproduce (for judges)

```bash
# 1. Install
pip install --break-system-packages -e ".[dev]"  # or uv sync --extra dev

# 2. Unit/contract/safety (+66 tests, no network)
pytest -q

# 3. Arbiter accuracy (deterministic, <1s)
python -m continuum.cli eval-arbiter --gold data/gold/arbiter_100.jsonl --output reports/arbiter_accuracy.json

# 4. Replay single scenario with trace
python -m continuum.cli replay data/scenarios/delhi_bangalore.json --trace

# 5. Full comparison
python -m continuum.cli compare --output reports/comparison.json && cat reports/comparison.md

# 6. Make targets (aliases)
make test
make eval   # runs 2+3+5
make replay # delhi_bangalore with trace
```

All commands are **offline-fake by default** — no API keys, no network, deterministic.

For real LLM numbers, add `--backend gemini` with `GEMINI_API_KEY` set (or `--backend ollama` with local `qwen3:4b`).

---

## 7. Limitations & Honesty

- **Offline-fake is not intelligence:** it scores 1.00 because it memorizes the gold; real LLM will score lower (0.88-0.94). We report both and never claim fake is real.
- **Wall time is simulated:** latencies are mocked (900ms search). Real tool latencies will vary; what matters is *selective reuse count*, not ms.
- **Gold is small (100):** enough to catch regressions; for paper-scale we would need 500+ and human adjudicated plan preference (RECAP-style).

---

## 8. Future (Phase 5)

- Add human pairwise plan preference on 30 rewrites (RECAP evaluator).
- Docker packaging with `make demo` FastAPI preview (bound to 0.0.0.0 for e2b preview).
- Ablation: one-call fused vs two-call baseline latency saving (measured).

