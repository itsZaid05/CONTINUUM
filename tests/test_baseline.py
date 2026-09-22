"""Phase 5 — the naive baseline agent must exhibit exactly the defects CONTINUUM prevents."""

from continuum.baseline import compare_with_baseline, replay_naive
from continuum.replay import replay_scenario


def _both(name):
    base = replay_naive(f"data/scenarios/{name}.json")
    cont = replay_scenario(f"data/scenarios/{name}.json")
    return base, cont, compare_with_baseline(f"data/scenarios/{name}.json", cont)


def test_delhi_naive_applies_stale():
    base, _, _ = _both("delhi_bangalore")
    assert base["stale_applied"] is True  # late Delhi result poisons Bangalore plan
    assert base["correct"] is False
    assert base["naive_llm_calls"] == 6  # 3 turns × 2 calls (extraction + classification)


def test_dont_book_it_naive_books_anyway():
    base, _, _ = _both("dont_book_it")
    assert base["retract_ignored"] is True
    assert base["correct"] is False


def test_timeout_naive_double_books():
    base, cont, row = _both("timeout_booking")
    assert base["double_booked"] is True  # blind retry, no ledger verify
    assert base["correct"] is False
    # CONTINUUM's win here is correctness, not speed — and the report says so
    assert row["saved_pct"] == 0.0
    assert row["continuum_correct"] is True


def test_retract_after_commit_naive_fakes_undo():
    base, _, _ = _both("retract_after_commit")
    assert base["dishonest_after_commit"] is True
    assert base["correct"] is False


def test_continuum_never_redoes_all_on_confident_turns():
    # CONTINUUM selective-invalidation: baseline analytic redo count vs continuum reused
    _, cont, _ = _both("delhi_bangalore")
    assert cont["reused"] >= 1  # morning constraint + Bangalore search survive
    base, _, _ = _both("delhi_bangalore")
    assert base["naive_redo_dispatches"] == 6  # 3 turns × (search+book) — wasteful by design


def test_all_scenarios_continuum_not_slower_than_analytic_baseline():
    for sc in (
        "delhi_bangalore",
        "dont_book_it",
        "retract_after_commit",
        "timeout_booking",
        "rapid_burst",
        "shadow_bangalore",
        "duplicate_result",
    ):
        s = replay_scenario(f"data/scenarios/{sc}.json")
        assert s["continuum_wall_ms"] <= s["baseline_wall_ms"]
