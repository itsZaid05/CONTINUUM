import json
from pathlib import Path

import pytest

from continuum.integrations.fdb import FDB_TOOL_NAMES, fdb_tool_manifests, load_benchmark


@pytest.fixture
def benchmark_file(tmp_path: Path) -> Path:
    payload = {
        "benchmark_name": "Full-Duplex-Bench-v3",
        "version": "3.0",
        "generated_at": "2026-04-15",
        "statistics": {
            "total_scenarios": 1,
            "domains": {"ecommerce_support": 1},
            "difficulty_distribution": {"hard": 1},
            "feature_distribution": {"SELF_CORRECTION": 1},
            "state_rollback_scenarios": 1,
            "multilingual_scenarios": 0,
            "avg_tool_calls_per_scenario": 3.0,
        },
        "scenarios": [
            {
                "id": "ecommerce_18",
                "domain": "ecommerce_support",
                "title": "Search, add, and track",
                "difficulty": "hard",
                "dialogue": [{"user": "find a mouse, add it, then track PO999"}],
                "disfluency_features": ["SELF_CORRECTION"],
                "expected_tool_calls": [
                    {"function": "search_products", "args": {"query": "gaming mouse"}},
                    {
                        "function": "add_to_cart",
                        "args": {"product_id": "$RESULT_0.cheapest_product_id", "quantity": 1},
                    },
                    {"function": "track_order", "args": {"order_id": "PO999"}},
                ],
                "num_expected_calls": 3,
                "state_rollback_test": True,
                "latency_profile": "normal",
            }
        ],
    }
    path = tmp_path / "benchmark_data_v2.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_official_benchmark_contract_preserves_ordered_repeated_calls(
    benchmark_file: Path,
) -> None:
    benchmark = load_benchmark(benchmark_file)
    scenario = benchmark.scenarios[0]
    assert benchmark.expected_call_count == 3
    assert [row.function for row in scenario.expected_tool_calls] == [
        "search_products",
        "add_to_cart",
        "track_order",
    ]
    assert benchmark.tool_names <= set(FDB_TOOL_NAMES)


def test_benchmark_summary_mismatch_is_rejected(benchmark_file: Path) -> None:
    raw = json.loads(benchmark_file.read_text(encoding="utf-8"))
    raw["statistics"]["total_scenarios"] = 100
    benchmark_file.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="total_scenarios"):
        load_benchmark(benchmark_file)


def test_all_twelve_fdb_tools_have_complete_safety_metadata() -> None:
    manifests = fdb_tool_manifests()
    assert tuple(row.name for row in manifests) == FDB_TOOL_NAMES
    assert len(manifests) == 12
    for manifest in manifests:
        assert manifest.arguments["type"] == "object"
        assert manifest.returns["type"] == "object"
        if manifest.state_changing:
            assert manifest.authorization == "fdb-v3-simulated-environment"
            assert manifest.postcondition
            assert manifest.cancellable is False
