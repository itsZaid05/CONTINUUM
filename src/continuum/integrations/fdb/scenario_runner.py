"""Credential-free contract runner for released FDB scenario call chains.

This executes annotated expected calls against the local bridge.  It is an
internal integration check, not an official model score; official evaluation
still requires released audio, LiveKit rooms, Gemini, and upstream evaluators.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .backend import FdbMockBackend
from .contracts import FdbBenchmark, FdbScenario, load_benchmark
from .telemetry import JsonlTelemetry
from .tool_bridge import FdbToolBridge

_REFERENCE = re.compile(r"^\$RESULT_(\d+)((?:\.[A-Za-z_]\w*|\[\d+\])*)$")
_TOKEN = re.compile(r"\.([A-Za-z_]\w*)|\[(\d+)\]")


def resolve_result_references(value: Any, results: list[dict[str, Any]]) -> Any:
    """Resolve every released ``$RESULT_n.path[index]`` expression recursively."""
    if isinstance(value, dict):
        return {key: resolve_result_references(item, results) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve_result_references(item, results) for item in value]
    if not isinstance(value, str):
        return value
    match = _REFERENCE.fullmatch(value)
    if match is None:
        return value
    index = int(match.group(1))
    if index >= len(results):
        raise ValueError(f"reference {value!r} names a result that is not available")
    current: Any = results[index]
    for token in _TOKEN.finditer(match.group(2)):
        field, array_index = token.groups()
        if field is not None:
            if not isinstance(current, dict) or field not in current:
                raise ValueError(f"reference {value!r} has no field {field!r}")
            current = current[field]
        else:
            if not isinstance(current, list):
                raise ValueError(f"reference {value!r} indexes a non-list value")
            current = current[int(array_index)]
    return current


async def execute_scenario(
    scenario: FdbScenario,
    *,
    telemetry_dir: str | Path,
) -> tuple[list[dict[str, Any]], FdbMockBackend, FdbToolBridge]:
    directory = Path(telemetry_dir)
    backend = FdbMockBackend("instant")
    telemetry = JsonlTelemetry(
        room_name=f"contract-{scenario.id}",
        path=directory / "telemetry.jsonl",
        official_path=directory / "agent_tool_calls.log",
        heartbeat_path=directory / "heartbeat.log",
    )
    bridge = FdbToolBridge(backend, telemetry, room_name=f"contract-{scenario.id}")
    results: list[dict[str, Any]] = []
    bridge.begin_candidate_turn()
    bridge.commit_turn("annotated_scenario")
    try:
        for index, expected in enumerate(scenario.expected_tool_calls):
            args = resolve_result_references(expected.args, results)
            if not isinstance(args, dict):  # pragma: no cover - guarded by contract model
                raise ValueError(f"{scenario.id} call {index} did not resolve to an object")
            result = await bridge.execute(
                expected.function,
                args,
                call_id=f"{scenario.id}-{index}",
            )
            results.append(result)
    finally:
        await bridge.close()
    return results, backend, bridge


async def execute_benchmark(
    benchmark: FdbBenchmark,
    *,
    telemetry_dir: str | Path,
) -> dict[str, Any]:
    failures: list[dict[str, str]] = []
    calls = 0
    dispatches = 0
    for scenario in benchmark.scenarios:
        try:
            results, backend, _ = await execute_scenario(
                scenario,
                telemetry_dir=telemetry_dir,
            )
            calls += len(results)
            dispatches += len(backend.dispatch_log)
        except Exception as exc:  # noqa: BLE001 - report every scenario, do not abort the suite
            failures.append({"scenario": scenario.id, "error": str(exc)})
    return {
        "kind": "internal_contract_check",
        "official_score": False,
        "benchmark_version": benchmark.version,
        "scenarios": len(benchmark.scenarios),
        "expected_calls": benchmark.expected_call_count,
        "executed_calls": calls,
        "backend_dispatches": dispatches,
        "failures": failures,
        "passed": not failures and calls == benchmark.expected_call_count,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("benchmark", type=Path, help="upstream v3/benchmark_data_v2.json")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/fdb_mock_contract.json"),
    )
    options = parser.parse_args(argv)
    benchmark = load_benchmark(options.benchmark)
    with tempfile.TemporaryDirectory(prefix="continuum-fdb-contract-") as temp:
        report = asyncio.run(execute_benchmark(benchmark, telemetry_dir=temp))
    options.output.parent.mkdir(parents=True, exist_ok=True)
    options.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
