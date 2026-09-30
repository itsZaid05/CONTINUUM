"""
Quickstart — 30-second tour of CONTINUUM Engineer A (Phase 2)

Phase 2: offline-fake + dense gate + LLM adapters (ollama/gemini/openai) with
temperature-scaled confidence and graceful fallback.

Run:
    python examples/quickstart.py
"""

from pathlib import Path

from continuum.branch_manager import BranchManager
from continuum.contracts import (
    ArbiterCategory,
    ArbiterDecision,
    Delta,
    DeltaOp,
    ExecutionNode,
    NodeStatus,
    Provenance,
)
from continuum.delta_arbiter import ArbiterBackend, arbitrate_offline_fake
from continuum.llm import build_fused_prompt, calibrate_confidence, parse_structured_json
from continuum.perception import perceive_text
from continuum.provenance import ProvenanceGraph
from continuum.replay import replay_scenario
from continuum.versioned_state import VersionedStore

print("=== 1. Perception FAST PATH (<200ms) ===")
out = perceive_text("Actually, Bangalore", version_in=1)
print(
    f"  text: 'Actually, Bangalore' → fast_ack: '{out.fast_ack}' latency {out.fast_ack_latency_ms}ms"
)
print(f"  guessed: {out.render_provenance}")

print("\n=== 2. Fused Delta+Arbiter (1 call) — offline-fake ===")
store = VersionedStore()
store.create_initial({"destination": "Delhi", "goal_domain": "flights"})
cur = store.current()
for utt in [
    "Actually, Bangalore",
    "…but keep the morning constraint",
    "Don't book it",
    "Forget flights, find trains",
    "Hmm, okay…",
]:
    d = arbitrate_offline_fake(utt, cur)
    print(f"  '{utt:35s}' → {d.category.value:14s} conf {d.confidence:.2f}  delta={d.delta}")

print("\n=== 2b. Phase 2 backends (dense / LLM) — same API, env-gated ===")
for backend in ["offline-fake", "dense", "ollama", "gemini", "openai"]:
    b = ArbiterBackend(backend)
    d = b.arbitrate("Actually, Bangalore", cur)
    print(
        f"  {backend:14s} → {d.category.value:14s} conf {d.confidence:.2f} model {d.model:30s} lat {d.latency_ms}ms"
    )
print(
    "  (dense/LLM fallback to offline-fake if no model/key — still 100% accuracy, latency shows gap)"
)

print("\n=== 2c. Temperature scaling + prompt ===")
prompt = build_fused_prompt("Actually, Bangalore", cur)
print(f"  prompt preview: {prompt[:120]}... (len {len(prompt)})")
for raw, cat in [(0.94, ArbiterCategory.RETRACT), (0.88, ArbiterCategory.MODIFY)]:
    scaled = calibrate_confidence(raw, cat, temperature=1.2)
    print(f"  raw {raw:.2f} {cat.value:14s} T=1.2 → {scaled:.3f} (conservative)")

j = parse_structured_json(
    'Here is JSON: {"category":"MODIFY","confidence":0.9,"delta":{"op":"replace","field":"destination"}} done'
)
print(f"  parse_structured_json → {j}")

print("\n=== 3. Versioned State V1→V2→V3 + merge_rapid ===")
store2 = VersionedStore()
v1 = store2.create_initial({})
print(f"  V1: {v1.state}")
v2 = store2.patch(
    v1.version,
    Delta(op=DeltaOp.REPLACE, field="destination", old_value=None, new_value="Delhi", span="Delhi"),
    ArbiterCategory.MODIFY,
)
print(f"  V2 (Delhi): {v2.state}")
v3 = store2.patch(
    v2.version,
    Delta(
        op=DeltaOp.REPLACE,
        field="destination",
        old_value="Delhi",
        new_value="Bangalore",
        span="Bangalore",
    ),
    ArbiterCategory.MODIFY,
)
print(f"  V3 (Bangalore): {v3.state}  parent={v3.parent}")
# rapid burst coalesce
store3 = VersionedStore()
vb = store3.create_initial({"destination": "Delhi"})
merged = store3.merge_rapid(
    vb.version,
    [
        (
            Delta(
                op=DeltaOp.REPLACE,
                field="destination",
                old_value="Delhi",
                new_value="Bangalore",
                span="Bangalore",
            ),
            ArbiterCategory.MODIFY,
            [],
        ),
        (
            Delta(
                op=DeltaOp.ADD,
                field="constraints",
                old_value=None,
                new_value={"time": "morning"},
                span="morning",
            ),
            ArbiterCategory.ADD_CONSTRAINT,
            [],
        ),
    ],
)
print(f"  MERGED burst: {merged.state}  status={merged.status.value}")

print("\n=== 4. Provenance selective invalidation ===")
g = ProvenanceGraph()

n_search = ExecutionNode(
    id="search:1:0",
    kind="search",
    provenance=Provenance(step_id="search-0", based_on=1),
    status=NodeStatus.COMPLETED,
)
n_filter = ExecutionNode(
    id="filter:1:1",
    kind="filter",
    provenance=Provenance(step_id="filter-1", based_on=1),
    status=NodeStatus.COMPLETED,
)
g.add(n_search)
g.add(n_filter)
print(f"  before: {[(n.id, n.status.value) for n in g.all_nodes()]}")
invalidated = g.invalidate_affected(2, ArbiterCategory.ADD_CONSTRAINT, "constraints")
print(f"  ADD_CONSTRAINT 'constraints' invalidated: {invalidated}  (only filter, search reused)")

print("\n=== 5. Branch budget (2 shadow, depth 3) ===")
bm = BranchManager()
dec = ArbiterDecision(category=ArbiterCategory.MODIFY, confidence=0.9, rationale="x")
for i in range(3):
    b = bm.spawn_shadow(1, dec)
    print(f"  spawn {i + 1}: {'ok ' + b.id if b else 'REFUSED (budget 2)'}")
b = bm.spawn_shadow(1, dec)
if b is None:
    print("  3rd correctly refused")

print("\n=== 6. Replay Delhi→Bangalore (46.9% saved) ===")
summary = replay_scenario(Path("data/scenarios/delhi_bangalore.json"))
print(
    f"  scenario: {summary['scenario']}  V{summary['current_version']}  {summary['saved_pct']}% saved"
)
print(f"  baseline {summary['baseline_wall_ms']}ms → continuum {summary['continuum_wall_ms']}ms")
print(f"  invalidated {summary['invalidated']}/{summary['dispatched']}  reused {summary['reused']}")
print(f"  trace events: {[e['event'] for e in summary['trace'][:5]]} ...")
# also demo LLM backend replay (fallback)
summary_llm = replay_scenario(Path("data/scenarios/delhi_bangalore.json"), backend="gemini")
print(
    f"  gemini backend (fallback) saved {summary_llm['saved_pct']}% model {summary_llm['backend']}"
)

print("\n=== 7. Eval harness ===")
print("  Run: python -m continuum.cli eval-arbiter --gold data/gold/arbiter_100.jsonl")
print(
    "       python -m continuum.cli eval-arbiter --backend dense --output reports/arbiter_dense.json"
)
print(
    "       python -m continuum.cli eval-arbiter --backend gemini --output reports/arbiter_gemini.json  # needs GEMINI_API_KEY"
)
print("       CONTINUUM_BACKEND=ollama python -m continuum.cli eval-arbiter")
print("  Reports: reports/arbiter_accuracy.md , reports/comparison.md")

print("\n✅ Quickstart done — Phase 2 works offline-fake deterministic, LLM adapters env-gated.")
print("Next: python -m continuum.cli replay data/scenarios/delhi_bangalore.json --trace")
print("      CONTINUUM_DENSE_DOWNLOAD=1 python examples/quickstart.py  # to test MiniLM if cached")
