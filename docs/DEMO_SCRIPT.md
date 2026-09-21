# Demo Script — 90-Second Walkthrough (for 5-min video)

> Goal: Show CONTINUUM beats naive redo-all on correctness, speed, and safety — not just happy path.

---

## 0. Title Card (5s)

**CONTINUUM — Interruptible Real-Time Agents**  
*Theme 05 • Engineer A: Understanding, Dialogue & Evaluation*  
One-liner: keeps agents consistent when humans change their minds.

Architecture diagram (Mermaid, from README). Highlight Engineer A in blue: Perception → Delta+Arbiter (1 call) → Versioned State → Provenance → Evaluation.

---

## 1. The Problem (10s)

User: "Book Delhi flights for next Monday morning" → agent searches Delhi (slow tool, 900ms).  
Mid-search, user interrupts: **"Actually, Bangalore"** — naive agent either ignores or restarts everything (wastes morning constraint).

Show naive baseline: Delhi search result arrives late → used despite stale → wrong booking.

---

## 2. Perception FAST PATH (10s)

Run:
```bash
python -m continuum.cli replay data/scenarios/delhi_bangalore.json --trace | head -n 40
```

Point out:
- `perception: fast_ack "Got it — updating your request…" latency 1ms` (<200ms ACK)
- `arbiter: category MODIFY confidence 0.94 delta destination Delhi→Bangalore` (one call emits both delta and category)

Text overlay: *Fused single LLM call saves ~800ms vs two-call baseline*.

---

## 3. Versioned State & Provenance (15s)

Trace shows:
- `patched: V2 destination Delhi → V3 destination Bangalore  invalidated [search:2:0] reused 0` — Delhi search invalidated, Bangalore search started, **morning constraint kept** (not re-asked).
- Later `ADD_CONSTRAINT keep morning → invalidated []` — only filter invalidated, search reused.

Stale gate:
- `tool_result search:2:0 based_on 2 gate DISCARD` — late Delhi result discarded.
- `tool_result search:3:1 based_on 3 gate APPLY` — Bangalore result applied.

Numbers: `baseline 2450ms → continuum 1300ms  46.9% saved` (from `reports/comparison.md`).

---

## 4. Retraction — The Safety Story (15s)

```bash
python -m continuum.cli replay data/scenarios/dont_book_it.json --trace | grep -E "retract|pruned|patched"
```

- "Don't book it" → `category RETRACT confidence 0.95 delta remove booking_instruction` → `retract_pruned invalidated [book:...] kept_search true`
- Search and options stay — can still show alternatives.

Then the hard case:

```bash
python -m continuum.cli replay data/scenarios/retract_after_commit.json --trace
```

- Ledger shows `COMMITTED` before retraction → response is honest: *"It's already booked (Ref BLR-11:03). Want me to cancel? (free within 24h)"* — never pretends undo.

---

## 5. Timeout — No Double-Book (10s)

```bash
python -m continuum.cli replay data/scenarios/timeout_booking.json --trace
```

- `timeout book → verify_after_timeout COMMITTED reused true` — checks existence instead of blind retry → no double booking.
- Test suite: `pytest tests/test_ledger.py -v` 10/10 pass.

---

## 6. Metrics Wall (10s)

Show `reports/arbiter_accuracy.md`:

| Category | F1 | Support |
| MODIFY | 1.00 | 20 |
| ADD_CONSTRAINT | 1.00 | 20 |
| RETRACT | 1.00 | 20 |
| NEW_GOAL | 1.00 | 20 |
| NOISE | 1.00 | 20 |
| **macro-F1** | **1.00** (offline-fake), ~0.90 real Gemini |

And `reports/comparison.md` table with 46.9% saved on Delhi scenario.

Text overlay: *Offline-fake deterministic for CI; real LLM measured separately — we report both.*

---

## 7. Branch Budget (5s, Phase 4 teaser)

```bash
python -m continuum.cli replay data/scenarios/shadow_bangalore.json --trace | grep invalidated
```

- Shadow branches: max 2, depth 3, READ/STAGE only, paused first when busy — budget enforced.
- `Branch cleanup p95 <150ms` (from tests/test_branch.py).

---

## 8. Closing (5s)

- `make test` → 66 tests, 0 failures, no network.
- `make eval` → all reports in <10s.
- Tag: `PRISM_GENAI_HACKATHON_Y2026`

CTA: `git clone … && make eval` — judges can reproduce in 60 seconds.

---

## Commands for Recording (copy-paste)

```bash
pytest -q
python -m continuum.cli eval-arbiter --gold data/gold/arbiter_100.jsonl --output reports/arbiter_accuracy.json
cat reports/arbiter_accuracy.md
python -m continuum.cli replay data/scenarios/delhi_bangalore.json --trace
python -m continuum.cli replay data/scenarios/dont_book_it.json --trace
python -m continuum.cli replay data/scenarios/timeout_booking.json --trace
python -m continuum.cli compare --output reports/comparison.json && cat reports/comparison.md
```

Record terminal with `asciinema` or VS Code.

---

## Fallback if Demo Fails

Offline-fake never fails (no network). If Gemini Key missing, demo still shows 1.00 accuracy table and trace — explain that fake is deterministic table for CI; real LLM line is in `docs/EVALUATION.md`.

