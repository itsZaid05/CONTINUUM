import hashlib

import pytest

from continuum.contracts import EffectStatus
from continuum.ledger import EffectLedger, effect_id_for


def test_prepare_and_commit():
    ledger = EffectLedger()
    eid = effect_id_for(1, "book", {"dest": "Bangalore"})
    rec = ledger.prepare(eid, "book", "hash")
    assert rec.status == EffectStatus.UNKNOWN
    ledger.commit(eid, result={"ref": "BLR-123"})
    assert rec.status == EffectStatus.COMMITTED
    assert rec.result["ref"] == "BLR-123"


def test_idempotency_same_key_no_double():
    ledger = EffectLedger()
    eid = effect_id_for(1, "book", {"dest": "Bangalore"})
    r1 = ledger.prepare(eid, "book", "h")
    r2 = ledger.prepare(eid, "book", "h")
    assert r1 is r2
    assert len(ledger.all()) == 1


def test_verify_after_timeout_reuses():
    ledger = EffectLedger()
    eid = effect_id_for(1, "book", {"x": 1})
    ledger.prepare(eid, "book", "h")
    # simulate exists check returns True (booking succeeded server-side)
    status = ledger.verify_after_timeout(eid, exists_fn=lambda _eid: True)
    assert status == EffectStatus.COMMITTED


def test_verify_after_timeout_failed():
    ledger = EffectLedger()
    eid = effect_id_for(1, "book", {"x": 2})
    ledger.prepare(eid, "book", "h")
    status = ledger.verify_after_timeout(eid, exists_fn=lambda _eid: False)
    assert status == EffectStatus.FAILED


def test_verify_without_checker_keeps_unknown():
    ledger = EffectLedger()
    eid = effect_id_for(2, "pay", {"amt": 100})
    ledger.prepare(eid, "pay", "h")
    status = ledger.verify_after_timeout(eid, exists_fn=None)
    assert status == EffectStatus.UNKNOWN


def test_effect_id_deterministic():
    a = effect_id_for(1, "search", {"q": "Delhi"})
    b = effect_id_for(1, "search", {"q": "Delhi"})
    c = effect_id_for(1, "search", {"q": "Bangalore"})
    assert a == b
    assert a != c
    assert len(a) == 16


# ---------------------------------------------------------------------------
# Phase 3 DoD — 10 injected timeout cases: verify-after-timeout, never
# blind-retry, never double-book. (mirrors `tool.sleep(3s)` + interrupt)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", list(range(10)))
def test_timeout_matrix_no_double_book(case: int):
    """Half the cases find the effect already committed (reuse); half find it
    absent (safe single retry). Ledger must never hold two records per key and
    a COMMITTED effect must never be re-dispatched."""
    led = EffectLedger()
    args = {"scenario": f"timeout_case_{case}"}
    eid = effect_id_for(version=3, tool="book", args=args)
    rec = led.prepare(eid, "book", hashlib.sha256(f"case{case}".encode()).hexdigest()[:12])
    assert rec.status == EffectStatus.UNKNOWN

    already_committed = case % 2 == 0
    exists_fn = (lambda _eid: already_committed) if case < 8 else None
    if exists_fn is None:
        # no checker available: stay UNKNOWN, caller must not blindly retry
        status = led.verify_after_timeout(eid)
        assert status == EffectStatus.UNKNOWN
        # tool-side confirmation arrives later → single commit
        status = led.verify_after_timeout(eid, exists_fn=lambda _eid: True)
        assert status == EffectStatus.COMMITTED
    else:
        status = led.verify_after_timeout(eid, exists_fn=exists_fn)
        assert status == (EffectStatus.COMMITTED if already_committed else EffectStatus.FAILED)

    # retry attempt after FAILED: same idempotency key → same record, never a second
    again = led.prepare(eid, "book", "hash2")
    assert again.effect_id == eid
    assert len(led.all()) == 1

    # a committed effect cannot be re-dispatched: re-verify keeps single COMMITTED
    if case % 2 == 0:
        assert led.verify_after_timeout(eid, exists_fn=lambda _e: True).value == "COMMITTED"
        assert len([r for r in led.all() if r.status == EffectStatus.COMMITTED]) == 1
        if already_committed:
            # double-book guard: no second record for same (version, tool, args)
            dup = effect_id_for(3, "book", args)
            assert dup == eid
