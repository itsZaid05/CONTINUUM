from continuum.versioned_state import VersionedStore
from continuum.contracts import ArbiterCategory, Delta, DeltaOp

def test_create_and_patch():
    store = VersionedStore()
    v1 = store.create_initial({"destination": "Delhi", "dates": "Monday"})
    assert v1.version == 1
    delta = Delta(op=DeltaOp.REPLACE, field="destination", old_value="Delhi", new_value="Bangalore", span="Actually, Bangalore")
    v2 = store.patch(v1.version, delta, ArbiterCategory.MODIFY)
    assert v2.version == 2
    assert v2.state["destination"] == "Bangalore"
    assert v2.state["dates"] == "Monday"
    assert v2.parent == 1

def test_noise_returns_base():
    store = VersionedStore()
    v1 = store.create_initial({"a": 1})
    v_same = store.patch(v1.version, Delta(op=None), ArbiterCategory.NOISE)
    assert v_same.version == 1
    assert v_same is v1

def test_patch_add_constraint_list():
    store = VersionedStore()
    v1 = store.create_initial({"destination": "Delhi"})
    delta = Delta(op=DeltaOp.ADD, field="constraints", old_value=None, new_value={"time": "morning"}, span="keep morning")
    v2 = store.patch(v1.version, delta, ArbiterCategory.ADD_CONSTRAINT)
    assert v2.state["constraints"] == {"time": "morning"}

def test_merge_rapid_last_wins():
    store = VersionedStore()
    v1 = store.create_initial({"destination": "Delhi"})
    d1 = (Delta(op=DeltaOp.REPLACE, field="destination", old_value="Delhi", new_value="Mumbai", span="Mumbai"), ArbiterCategory.MODIFY, [])
    d2 = (Delta(op=DeltaOp.REPLACE, field="destination", old_value="Mumbai", new_value="Bangalore", span="Bangalore"), ArbiterCategory.MODIFY, [])
    v2 = store.merge_rapid(v1.version, [d1, d2])
    assert v2.state["destination"] == "Bangalore"
    assert v2.status.value == "MERGED"

def test_nested_set_and_remove():
    store = VersionedStore()
    v1 = store.create_initial({"a": {"b": 1}})
    d = Delta(op=DeltaOp.REPLACE, field="a.b", old_value=1, new_value=2, span="x")
    v2 = store.patch(v1.version, d, ArbiterCategory.MODIFY)
    assert v2.state["a"]["b"] == 2
    d2 = Delta(op=DeltaOp.REMOVE, field="a.b", old_value=2, new_value=None, span="x")
    v3 = store.patch(v2.version, d2, ArbiterCategory.RETRACT)
    assert "b" not in v3.state["a"]

def test_history_sorted():
    store = VersionedStore()
    v1 = store.create_initial({"x": 1})
    v2 = store.patch(v1.version, Delta(op=DeltaOp.REPLACE, field="x", old_value=1, new_value=2, span="x"), ArbiterCategory.MODIFY)
    hist = store.history()
    assert [h.version for h in hist] == [1, 2]

def test_wal_roundtrip(tmp_path):
    wal = tmp_path / "wal.jsonl"
    store = VersionedStore(wal_path=wal)
    v1 = store.create_initial({"d": "Delhi"})
    v2 = store.patch(v1.version, Delta(op=DeltaOp.REPLACE, field="d", old_value="Delhi", new_value="Bangalore", span="b"), ArbiterCategory.MODIFY)
    assert wal.exists()
    # new store loads
    store2 = VersionedStore(wal_path=wal)
    store2.load_wal()
    assert store2.get(1).state["d"] == "Delhi"
    assert store2.get(2).state["d"] == "Bangalore"
