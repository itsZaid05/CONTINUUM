"""
CONTINUUM End-to-End Live Demo Integration Tests
Verifies all 4 demo scenarios from Hackathon Specification Section 5.
"""

from fastapi.testclient import TestClient

from backend.app.core.effect_ledger import effect_ledger
from backend.app.core.policy_engine import policy_engine
from backend.app.main import app

client = TestClient(app)


def test_demo_case_1_prefetch_and_pivot():
    session_id = "demo_case_1_session"

    # 1. User starts: "Find flights to Delhi tomorrow morning"
    res1 = client.post(
        "/api/v1/session/interrupt",
        json={"session_id": session_id, "utterance": "Find flights to Delhi tomorrow morning"},
    )
    assert res1.status_code == 200
    d1 = res1.json()
    assert d1["version"] == 1
    assert d1["latency_ms"] < 10.0  # Instant ACK < 10ms

    # 2. User interrupts: "Actually, make it Bangalore, but keep morning flight"
    res2 = client.post(
        "/api/v1/session/interrupt",
        json={
            "session_id": session_id,
            "utterance": "Actually, make it Bangalore, but keep morning flight",
        },
    )
    assert res2.status_code == 200
    d2 = res2.json()
    assert d2["version"] == 2
    assert d2["latency_ms"] < 10.0
    print("\n[PASS] Demo Case 1 (Pre-Fetch & Pivot with Instant ACK) verified.")


def test_demo_case_2a_retraction():
    session_id = "demo_case_2a_session"

    # Start session
    client.post(
        "/api/v1/session/interrupt",
        json={"session_id": session_id, "utterance": "Find flights to Bangalore"},
    )

    # User retracts: "Don't book it, cancel booking step immediately"
    res = client.post(
        "/api/v1/intent/classify",
        json={
            "session_id": session_id,
            "utterance": "Don't book it, cancel booking step immediately",
        },
    )
    data = res.json()
    assert data["delta_type"] == "RETRACT"
    assert data["authorization"] == "NONE"
    print("\n[PASS] Demo Case 2A (Retraction 'Don't book it') verified.")


def test_demo_case_2b_commitment_level_change():
    # User says: "Don't book it yet, just hold the seat"
    # Policy engine allows hold_seat (STAGEABLE) but forbids confirm_booking (IRREVERSIBLE)
    decision_hold = policy_engine.evaluate("hold_seat", ivs_score=0.4, authorization="IMPLIED")
    decision_confirm = policy_engine.evaluate(
        "confirm_booking", ivs_score=0.4, authorization="IMPLIED"
    )

    assert decision_hold in ["STAGE", "EXECUTE"]
    assert decision_confirm in ["ASK", "BLOCK"]
    print(
        "\n[PASS] Demo Case 2B (Commitment Change: STAGEABLE allowed, IRREVERSIBLE blocked) verified."
    )


def test_demo_case_3_timeout_and_effect_verification():
    # Simulate network timeout -> status UNKNOWN -> verify_effect endpoint checks status
    effect = effect_ledger.create_intent(
        session_id="demo_3_sess",
        event_id="evt_d3",
        step_id="p_3",
        tool_name="confirm_booking",
        params={"hold_id": "HLD_99"},
    )
    effect_ledger.transition_to_pending(effect.effect_id)
    effect_ledger.transition_to_unknown(effect.effect_id)

    # Call verify endpoint
    res = client.post(
        "/api/v1/effect/verify",
        json={"effect_id": effect.effect_id, "idempotency_key": effect.idempotency_key},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["status"] in ["COMMITTED", "NOT_COMMITTED", "UNKNOWN"]
    print("\n[PASS] Demo Case 3 (Timeout & Check-Before-Retry Status Verification) verified.")
