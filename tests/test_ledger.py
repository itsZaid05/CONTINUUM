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
