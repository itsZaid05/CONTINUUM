from pathlib import Path
from continuum.replay import replay_scenario

def test_delhi_bangalore_replay():
    summary = replay_scenario(Path("data/scenarios/delhi_bangalore.json"), backend="offline-fake", trace=False)
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
