"""
Phase 3 Verification Tests (Policy Engine, Effect Ledger, Idempotency & Safety Matrix)
"""

from backend.app.core.effect_ledger import effect_ledger
from backend.app.core.policy_engine import policy_engine
from backend.app.tools.registry import get_authoritative_risk


def test_authoritative_tool_registry():
    assert get_authoritative_risk("search_flights") == "FREE"
    assert get_authoritative_risk("hold_seat") == "STAGEABLE"
    assert get_authoritative_risk("modify_booking") == "MUTATING"
    assert get_authoritative_risk("confirm_booking") == "IRREVERSIBLE"
    # Unknown tool defaults to safest tier IRREVERSIBLE
    assert get_authoritative_risk("dangerous_unknown_tool") == "IRREVERSIBLE"
    print("\n[PASS] Authoritative Tool Registry verified.")


def test_policy_engine_safety_matrix():
    # 1. FREE actions (search_flights) can execute across any IVS
    assert (
        policy_engine.evaluate("search_flights", ivs_score=0.1, authorization="NONE") == "EXECUTE"
    )
    assert (
        policy_engine.evaluate("search_flights", ivs_score=0.8, authorization="NONE") == "EXECUTE"
    )

    # 2. STAGEABLE actions (hold_seat)
    assert policy_engine.evaluate("hold_seat", ivs_score=0.2, authorization="IMPLIED") == "EXECUTE"
    assert policy_engine.evaluate("hold_seat", ivs_score=0.5, authorization="IMPLIED") == "STAGE"
    assert policy_engine.evaluate("hold_seat", ivs_score=0.8, authorization="IMPLIED") == "HOLD"

    # 3. MUTATING actions (modify_booking)
    assert (
        policy_engine.evaluate("modify_booking", ivs_score=0.2, authorization="IMPLIED")
        == "EXECUTE"
    )
    assert (
        policy_engine.evaluate("modify_booking", ivs_score=0.5, authorization="IMPLIED") == "STAGE"
    )
    assert policy_engine.evaluate("modify_booking", ivs_score=0.8, authorization="IMPLIED") == "ASK"

    # 4. IRREVERSIBLE actions (confirm_booking) - Deterministic Safety Invariant:
    # Requires EXPLICIT Auth AND IVS < 0.6
    assert (
        policy_engine.evaluate("confirm_booking", ivs_score=0.2, authorization="EXPLICIT")
        == "EXECUTE"
    )
    assert (
        policy_engine.evaluate("confirm_booking", ivs_score=0.2, authorization="IMPLIED") == "ASK"
    )
    assert (
        policy_engine.evaluate("confirm_booking", ivs_score=0.7, authorization="EXPLICIT")
        == "BLOCK"
    )  # High IVS blocks!
    assert policy_engine.evaluate("confirm_booking", ivs_score=0.7, authorization="NONE") == "BLOCK"
    print("\n[PASS] Policy Engine Deterministic Safety Matrix verified.")


def test_shadow_branch_read_only_rule():
    # Shadow branches MUST be FREE (read-only) and only execute when IVS < 0.3
    assert (
        policy_engine.evaluate("search_cabs", ivs_score=0.2, authorization="NONE", is_shadow=True)
        == "EXECUTE"
    )
    assert (
        policy_engine.evaluate("search_cabs", ivs_score=0.5, authorization="NONE", is_shadow=True)
        == "BLOCK"
    )
    assert (
        policy_engine.evaluate(
            "confirm_booking", ivs_score=0.1, authorization="EXPLICIT", is_shadow=True
        )
        == "BLOCK"
    )
    print("\n[PASS] Shadow Branch Read-Only & IVS Invariant verified.")


def test_two_phase_effect_ledger_and_idempotency():
    session_id = "sess_ledger_001"
    event_id = "evt_001"
    step_id = "p_3"
    params = {"hold_id": "HLD_123", "flight_id": "FL_BLR_702"}

    # 1. Create intent
    record = effect_ledger.create_intent(session_id, event_id, step_id, "confirm_booking", params)
    assert record.status == "INTENT"
    assert record.idempotency_key.startswith("idemp_")

    # 2. Duplicate submission test -> should return SAME record and idempotency key
    record_dup = effect_ledger.create_intent(
        session_id, event_id, step_id, "confirm_booking", params
    )
    assert record_dup.effect_id == record.effect_id
    assert record_dup.idempotency_key == record.idempotency_key

    # 3. Transition to PENDING before dispatch
    effect_ledger.transition_to_pending(record.effect_id)
    assert record.status == "PENDING"

    # 4. Simulate timeout / error -> UNKNOWN status
    effect_ledger.transition_to_unknown(record.effect_id)
    assert record.status == "UNKNOWN"

    # 5. External status verification -> COMMITTED
    effect_ledger.transition_to_committed(record.effect_id, external_operation_id="ext_tx_8899")
    assert record.status == "COMMITTED"
    assert record.external_operation_id == "ext_tx_8899"
    print("\n[PASS] Two-Phase Effect Ledger, Idempotency Hashing & Recovery lifecycle verified.")
