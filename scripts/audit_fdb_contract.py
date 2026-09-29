#!/usr/bin/env python3
"""Fail fast when the pinned upstream FDB-v3 contract drifts."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from continuum.integrations.fdb import FDB_TOOL_NAMES, load_benchmark

PINNED_REVISION = "3e799c45a045256f47d5f1c9cda90157e2d2ec9e"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=Path(".artifacts/Full-Duplex-Bench"),
        help="Checkout of DanielLin94144/Full-Duplex-Bench",
    )
    args = parser.parse_args()
    source = args.source_dir.resolve()
    benchmark_path = source / "v3" / "benchmark_data_v2.json"
    benchmark = load_benchmark(benchmark_path)
    revision = subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "HEAD"], text=True
    ).strip()

    checks = {
        "revision": revision == PINNED_REVISION,
        "scenario_count": len(benchmark.scenarios) == 100,
        "expected_call_count": benchmark.expected_call_count == 154,
        "tool_contract": benchmark.tool_names == set(FDB_TOOL_NAMES),
        "rollback_count": sum(row.state_rollback_test for row in benchmark.scenarios) == 21,
    }
    report = {
        "source": str(source),
        "revision": revision,
        "benchmark_version": benchmark.version,
        "scenarios": len(benchmark.scenarios),
        "expected_tool_calls": benchmark.expected_call_count,
        "tools": sorted(benchmark.tool_names),
        "checks": checks,
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
