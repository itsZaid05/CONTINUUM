from pathlib import Path

from continuum.replay import replay_scenario


def test_delhi_bangalore_replay():
    summary = replay_scenario(
        Path("data/scenarios/delhi_bangalore.json"), backend="offline-fake", trace=False
    )
    assert summary["scenario"] == "delhi_bangalore"
    # Should have advanced version beyond 1
    assert summary["current_version"] >= 2
    # At least one invalidation (Delhi search invalidated when Bangalore patched)
    assert summary["invalidated"] >= 1
    # Continuum faster than baseline
    assert summary["continuum_wall_ms"] <= summary["baseline_wall_ms"]
    # Check versions history includes Bangalore
    state_last = summary["versions"][-1]["state"]
    assert "Bangalore" in str(state_last) or "bangalore" in str(state_last).lower()


def test_dont_book_retract():
    summary = replay_scenario(Path("data/scenarios/dont_book_it.json"), backend="offline-fake")
    # Should have pruned book node but kept search
    # Graph should have invalidated book
    assert summary["scenario"] == "dont_book_it"
    # Check trace has retract_pruned event
    events = [e["event"] for e in summary["trace"]]
    assert "retract_pruned" in events or "patched" in events


def test_timeout_verify():
    summary = replay_scenario(Path("data/scenarios/timeout_booking.json"), backend="offline-fake")
    events = [e["event"] for e in summary["trace"]]
    assert "timeout" in events
    assert "verify_after_timeout" in events


def test_rapid_burst():
    summary = replay_scenario(Path("data/scenarios/rapid_burst.json"), backend="offline-fake")
    assert summary["scenario"] == "rapid_burst"
    assert summary["current_version"] >= 2


def test_stale_discard():
    # Delhi search result based_on 1 after current_version 2 should be discarded
    summary = replay_scenario(Path("data/scenarios/delhi_bangalore.json"), backend="offline-fake")
    stale_events = [e for e in summary["trace"] if e.get("event") == "stale_discarded"]
    assert len(stale_events) >= 1  # Delhi result discarded


def test_shadow_bangalore_reused_wasted():
    """Phase 4 DoD: 1 reused, 1 wasted, cleanup <150ms, primary ≤5% slower."""
    summary = replay_scenario(Path("data/scenarios/shadow_bangalore.json"), backend="offline-fake")
    m = summary["shadow_metrics"]
    assert m["spawned"] == 2
    assert m["promoted"] == 1 and m["discarded"] == 1
    assert m["reused_pct"] == 50.0 and m["wasted_pct"] == 50.0
    assert m["cleanup_p95_ms"] < 150.0
    assert m["primary_slowdown_pct"] <= 5.0
    events = [e["event"] for e in summary["trace"]]
    assert events.count("shadow_spawn") == 2
    assert "shadow_promote" in events and "shadow_discard" in events
    # other scenarios don't waste budget
    other = replay_scenario(Path("data/scenarios/delhi_bangalore.json"), backend="offline-fake")
    assert other["shadow_metrics"]["spawned"] == 0


def test_honest_retract_after_commit():
    """Phase 3 DoD: 'don't book it' post-commit → honest, no fake undo, no state patch."""
    summary = replay_scenario(Path("data/scenarios/retract_after_commit.json"))
    events = summary["trace"]
    honest = [e for e in events if e["event"] == "honest_retract"]
    assert len(honest) == 1
    assert honest[0]["ref"] == "BLR-11:03"
    assert honest[0]["applied"] is False and honest[0]["cancel_offer"] is True
    dialogues = [e["text"] for e in events if e["event"] == "dialogue"]
    assert any("already booked" in t for t in dialogues)
    assert summary["current_version"] == 1  # state untouched — nothing was "undone"


def test_ablation_stale_gate_leaks():
    """Without the stale gate, the late Delhi result leaks into V2 state."""
    guarded = replay_scenario(Path("data/scenarios/delhi_bangalore.json"))
    leaky = replay_scenario(Path("data/scenarios/delhi_bangalore.json"), disable_stale_gate=True)
    assert guarded["stale_leaks"] == 0
    assert leaky["stale_leaks"] >= 1
    assert any(e["event"] == "stale_applied_ablation" for e in leaky["trace"])


def test_duplicate_result_ignored():
    """R-02 class: identical result delivered twice → applied once, second ignored."""
    summary = replay_scenario(Path("data/scenarios/duplicate_result.json"))
    events = [e["event"] for e in summary["trace"]]
    assert events.count("duplicate_result_ignored") == 1
    assert summary["duplicate_ignored"] == 1
    applies = [e for e in summary["trace"] if e["event"] == "tool_result" and e["gate"] == "APPLY"]
    assert len(applies) == 1  # only one application despite two deliveries
