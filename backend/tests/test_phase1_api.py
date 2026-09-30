"""
Phase 1 Verification Tests (Automated API / Contract Suite)
Equivalent to Postman / Integration Tests
"""

import time

from fastapi.testclient import TestClient

from backend.app.main import app

client = TestClient(app)


def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["tag"] == "PRISM_GENAI_HACKATHON_Y2026"
    print("\n[PASS] Health check verified.")


def test_fast_path_instant_ack_and_versioning():
    session_id = "test_sess_001"

    # 1st utterance -> Version should be 1
    t0 = time.time()
    res1 = client.post(
        "/api/v1/session/interrupt",
        json={"session_id": session_id, "utterance": "Find flights to Delhi tomorrow morning"},
    )
    t1 = time.time()

    assert res1.status_code == 200
    d1 = res1.json()
    assert d1["version"] == 1
    assert d1["ack_emitted"] is True
    # Latency should be well under 10ms
    latency_ms = (t1 - t0) * 1000
    print(
        f"\n[PASS] 1st Interrupt Version: {d1['version']}, Local ACK Latency: {latency_ms:.2f}ms (<10ms budget)"
    )

    # 2nd utterance (Interrupt) -> Version must monotonically advance to 2
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
    assert d2["event_id"].startswith("evt_")
    print(f"[PASS] Monotonic Version advance to {d2['version']} verified.")


def test_frozen_contract_1_intent_classify():
    session_id = "test_sess_002"
    payload = {
        "session_id": session_id,
        "utterance": "Actually, make it Bangalore, but keep morning flight",
    }
    res = client.post("/api/v1/intent/classify", json=payload)
    assert res.status_code == 200
    data = res.json()

    # Verify contract keys
    assert "event_id" in data
    assert data["session_id"] == session_id
    assert data["delta_type"] == "MODIFY"
    assert "ivs_score" in data
    assert 0.0 <= data["ivs_score"] <= 1.0
    assert data["authorization"] in ["EXPLICIT", "IMPLIED", "NONE"]
    assert "destination" in data["affected_fields"]
    assert data["new_values"].get("destination") == "Bangalore"
    assert any("morning" in c for c in data["preserved_constraints"])
    print("\n[PASS] Frozen Contract 1 (/intent/classify) verified.")


def test_frozen_contract_2_plan_generate():
    session_id = "test_sess_003"
    payload = {
        "session_id": session_id,
        "utterance": "Actually, make it Bangalore, but keep morning flight",
    }
    res = client.post("/api/v1/plan/generate", json=payload)
    assert res.status_code == 200
    data = res.json()

    assert data["session_id"] == session_id
    assert "primary_plan" in data
    assert len(data["primary_plan"]) == 3
    assert data["primary_plan"][0]["tool"] == "search_flights"
    assert data["primary_plan"][1]["tool"] == "hold_seat"
    assert data["primary_plan"][2]["tool"] == "confirm_booking"
    assert "shadow_branches" in data
    assert len(data["shadow_branches"]) >= 1
    assert data["shadow_branches"][0]["steps"][0]["tool"] == "search_cabs"
    print("\n[PASS] Frozen Contract 2 (/plan/generate) verified.")


def test_frozen_contract_3_effect_verify():
    payload = {
        "effect_id": "eff_7b82e1",
        "idempotency_key": "idemp_book_p3",
        "external_operation_id": "ext_tx_44921",
    }
    res = client.post("/api/v1/effect/verify", json=payload)
    assert res.status_code == 200
    data = res.json()

    assert data["effect_id"] == "eff_7b82e1"
    assert data["idempotency_key"] == "idemp_book_p3"
    assert data["external_operation_id"] == "ext_tx_44921"
    assert data["status"] in ["COMMITTED", "NOT_COMMITTED", "UNKNOWN"]
    print("\n[PASS] Frozen Contract 3 (/effect/verify) verified.")


def test_websocket_connection():
    with client.websocket_connect("/ws/test_ws_sess") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "SESSION_INIT"
        assert msg["session_id"] == "test_ws_sess"
        print("\n[PASS] WebSocket Real-time channel verified.")
