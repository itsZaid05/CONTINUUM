from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from continuum.integrations.fdb.adapter import LiveKitSessionAdapter
from continuum.integrations.fdb.backend import FdbMockBackend
from continuum.integrations.fdb.provider import (
    GeminiNativeAudioProvider,
    NativeAudioConfig,
    NativeAudioProvider,
    ProviderConfigurationError,
)
from continuum.integrations.fdb.scenario_runner import resolve_result_references
from continuum.integrations.fdb.telemetry import JsonlTelemetry
from continuum.integrations.fdb.tool_bridge import BridgeExecutionError, FdbToolBridge
from continuum.lifecycle import CallStatus
from continuum.tools import ToolManifest


def telemetry(tmp_path: Path, room: str = "room-1") -> JsonlTelemetry:
    return JsonlTelemetry(
        room_name=room,
        path=tmp_path / "telemetry.jsonl",
        official_path=tmp_path / "official.jsonl",
        heartbeat_path=tmp_path / "heartbeat.log",
    )


def committed_bridge(backend: Any, tmp_path: Path) -> FdbToolBridge:
    bridge = FdbToolBridge(backend, telemetry(tmp_path), room_name="room-1")
    bridge.begin_candidate_turn()
    bridge.commit_turn("test_final")
    return bridge


async def test_bridge_validates_executes_and_writes_official_shape(tmp_path: Path) -> None:
    backend = FdbMockBackend("instant")
    bridge = committed_bridge(backend, tmp_path)
    result = await bridge.execute(
        "search_flights",
        {"destination": "Tokyo", "date": "July 15"},
        call_id="lk-call-1",
    )
    await bridge.close()

    assert result["flights"][0]["flight_id"] == "FL123"
    assert bridge.history[0].status == CallStatus.COMPLETED
    assert bridge.history[0].current_call_id == "lk-call-1"
    row = json.loads((tmp_path / "official.jsonl").read_text().strip())
    assert row["room"] == "room-1"
    assert row["call"]["function"] == "search_flights"
    assert row["call"]["args"] == {"destination": "Tokyo", "date": "July 15"}
    assert row["call"]["timestamp_end"] >= row["call"]["timestamp_start"]


async def test_invalid_arguments_never_dispatch_or_reach_official_log(tmp_path: Path) -> None:
    backend = FdbMockBackend("instant")
    bridge = committed_bridge(backend, tmp_path)
    with pytest.raises(BridgeExecutionError, match="required property"):
        await bridge.execute("track_order", {})
    assert backend.dispatch_log == []
    assert not (tmp_path / "official.jsonl").exists()


async def test_candidate_only_turn_cannot_dispatch(tmp_path: Path) -> None:
    backend = FdbMockBackend("instant")
    bridge = FdbToolBridge(backend, telemetry(tmp_path), room_name="room-1")
    bridge.begin_candidate_turn()
    with pytest.raises(BridgeExecutionError, match="final transcript or EOT"):
        await bridge.execute("track_order", {"order_id": "PARTIAL"})
    assert backend.dispatch_log == []


async def test_mutation_is_effect_deduplicated_but_both_model_calls_are_audited(
    tmp_path: Path,
) -> None:
    backend = FdbMockBackend("instant")
    bridge = committed_bridge(backend, tmp_path)
    args = {"product_id": "PROD1", "quantity": 2}
    first = await bridge.execute("add_to_cart", args, call_id="one")
    second = await bridge.execute("add_to_cart", args, call_id="duplicate")

    assert first == second
    assert len(backend.dispatch_log) == 1
    assert bridge.history[1].detail == "deduplicated committed effect"
    assert len((tmp_path / "official.jsonl").read_text().splitlines()) == 2


async def test_concurrent_duplicate_mutation_is_dispatched_once(tmp_path: Path) -> None:
    backend = FdbMockBackend("fast", seed=1)
    bridge = committed_bridge(backend, tmp_path)
    args = {"product_id": "PROD1", "quantity": 2}
    first, second = await asyncio.gather(
        bridge.execute("add_to_cart", args, call_id="one"),
        bridge.execute("add_to_cart", args, call_id="two"),
    )
    assert first == second
    assert len(backend.dispatch_log) == 1
    assert all(row.status == CallStatus.COMPLETED for row in bridge.history)


class _TimeoutAfterCommitBackend:
    def __init__(self) -> None:
        self.result = {
            "status": "success",
            "product_id": "P1",
            "quantity": 1,
            "cart_total": 99.99,
        }

    async def call(self, tool: str, args: dict[str, Any], *, idempotency_key: str | None = None):
        raise TimeoutError("response lost")

    async def verify(self, idempotency_key: str):
        return dict(self.result)


async def test_mutation_timeout_is_verified_without_blind_retry(tmp_path: Path) -> None:
    backend = _TimeoutAfterCommitBackend()
    bridge = committed_bridge(backend, tmp_path)
    result = await bridge.execute("add_to_cart", {"product_id": "P1", "quantity": 1})
    assert result == backend.result
    assert bridge.history[0].status == CallStatus.COMPLETED
    assert bridge.history[0].detail == "verified after timeout"
    assert len(bridge.history[0].attempts) == 1


class _RetryingReadBackend:
    def __init__(self) -> None:
        self.calls = 0

    async def call(self, tool: str, args: dict[str, Any], *, idempotency_key: str | None = None):
        self.calls += 1
        if self.calls < 3:
            raise TimeoutError("temporary")
        return {"status": "success", "order_id": args["order_id"], "shipping_status": "ready"}

    async def verify(self, idempotency_key: str):
        return None


async def test_read_retries_use_one_operation_and_fresh_attempt_ids(tmp_path: Path) -> None:
    backend = _RetryingReadBackend()
    bridge = FdbToolBridge(
        backend,
        telemetry(tmp_path),
        room_name="room-1",
        retry_backoff_s=0,
    )
    bridge.commit_turn("test_eot")
    result = await bridge.execute("track_order", {"order_id": "A1"}, call_id="first")
    lifecycle = bridge.history[0]
    assert result["order_id"] == "A1"
    assert lifecycle.status == CallStatus.COMPLETED
    assert len(lifecycle.attempts) == 3
    assert len({attempt.call_id for attempt in lifecycle.attempts}) == 3


class _BadPostconditionBackend:
    async def call(self, tool: str, args: dict[str, Any], *, idempotency_key: str | None = None):
        return {
            "status": "success",
            "product_id": "WRONG",
            "quantity": args["quantity"],
            "cart_total": 99.99,
        }

    async def verify(self, idempotency_key: str):
        return None


async def test_failed_postcondition_never_becomes_completed(tmp_path: Path) -> None:
    bridge = committed_bridge(_BadPostconditionBackend(), tmp_path)
    with pytest.raises(BridgeExecutionError, match="postcondition"):
        await bridge.execute("add_to_cart", {"product_id": "P1", "quantity": 1})
    assert bridge.history[0].status == CallStatus.UNKNOWN


class _BlockingReadBackend:
    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def call(self, tool: str, args: dict[str, Any], *, idempotency_key: str | None = None):
        self.started.set()
        await asyncio.sleep(60)
        return {"status": "success", "order_id": args["order_id"], "shipping_status": "late"}

    async def verify(self, idempotency_key: str):
        return None


async def test_barge_in_cancels_only_cancellable_read(tmp_path: Path) -> None:
    backend = _BlockingReadBackend()
    bridge = committed_bridge(backend, tmp_path)
    task = asyncio.create_task(bridge.execute("track_order", {"order_id": "A1"}))
    await backend.started.wait()
    assert await bridge.cancel_cancellable() == 1
    with pytest.raises(asyncio.CancelledError):
        await task
    assert bridge.history[0].status == CallStatus.CANCELLED


class _BlockingMutationBackend:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def call(self, tool: str, args: dict[str, Any], *, idempotency_key: str | None = None):
        self.started.set()
        await self.release.wait()
        return {
            "status": "success",
            "product_id": args["product_id"],
            "quantity": args["quantity"],
            "cart_total": 99.99,
        }

    async def verify(self, idempotency_key: str):
        return None


async def test_shutdown_drains_non_cancellable_mutation(tmp_path: Path) -> None:
    backend = _BlockingMutationBackend()
    bridge = committed_bridge(backend, tmp_path)
    execution = asyncio.create_task(
        bridge.execute("add_to_cart", {"product_id": "P1", "quantity": 1})
    )
    await backend.started.wait()
    assert await bridge.cancel_cancellable("barge-in") == 0
    closing = asyncio.create_task(bridge.close(timeout_s=1))
    await asyncio.sleep(0)
    assert not closing.done()
    backend.release.set()
    assert (await execution)["product_id"] == "P1"
    await closing
    assert bridge.history[0].status == CallStatus.COMPLETED


async def test_adapter_commits_only_final_or_end_of_turn_and_emits_barge_in(
    tmp_path: Path,
) -> None:
    bridge = FdbToolBridge(FdbMockBackend("instant"), telemetry(tmp_path), room_name="room-1")
    adapter = LiveKitSessionAdapter(bridge, telemetry(tmp_path))
    adapter.on_user_input_transcribed(SimpleNamespace(transcript="track A", is_final=False))
    assert adapter.speech.committed == []
    adapter.on_user_input_transcribed(SimpleNamespace(transcript="track A1", is_final=True))
    assert [row.text for row in adapter.speech.committed] == ["track A1"]
    adapter.on_user_state_changed(SimpleNamespace(new_state="speaking"))
    await asyncio.sleep(0)
    await adapter.close()


def test_result_schema_validation_is_strict() -> None:
    manifest = ToolManifest(
        name="strict",
        returns={
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
            "additionalProperties": False,
        },
    )
    assert manifest.validate_result({"ok": True}) == {"ok": True}
    with pytest.raises(ValueError, match="invalid result"):
        manifest.validate_result({"ok": "yes"})
    with pytest.raises(ValueError, match="Additional properties"):
        manifest.validate_result({"ok": True, "invented": 1})


def test_nested_official_result_references_are_resolved() -> None:
    results = [
        {
            "apartments": [{"address": "100 Main"}],
            "products": [{"product_id": "PROD1"}],
        }
    ]
    assert resolve_result_references("$RESULT_0.apartments[0].address", results) == "100 Main"
    assert resolve_result_references(
        {"product_id": "$RESULT_0.products[0].product_id", "quantity": 2}, results
    ) == {"product_id": "PROD1", "quantity": 2}


def test_provider_interface_and_missing_key(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = GeminiNativeAudioProvider()
    assert isinstance(provider, NativeAudioProvider)
    assert provider.name == "gemini_native_audio"
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    with pytest.raises(ProviderConfigurationError, match="GOOGLE_API_KEY"):
        provider.build_model(NativeAudioConfig())


async def test_livekit_tools_are_generated_from_all_canonical_manifests(tmp_path: Path) -> None:
    pytest.importorskip("livekit.agents")
    from livekit.agents import RunContext, llm
    from livekit.agents.llm.utils import prepare_function_arguments

    from continuum.integrations.fdb.livekit_tools import make_livekit_tools

    bridge = FdbToolBridge(FdbMockBackend("instant"), telemetry(tmp_path), room_name="room-1")
    tools = make_livekit_tools(bridge)
    assert len(tools) == 12
    assert {tool.info.name for tool in tools} == set(
        bridge.registry.get(name).name
        for name in (
            "search_flights",
            "book_flight",
            "update_identity_doc",
            "get_card_benefits",
            "get_exchange_rate",
            "modify_autopay",
            "search_apartments",
            "calculate_commute",
            "update_search_filter",
            "track_order",
            "search_products",
            "add_to_cart",
        )
    )
    track = next(tool for tool in tools if tool.info.name == "track_order")
    assert track.info.raw_schema["parameters"] == bridge.registry.get("track_order").arguments

    call = llm.FunctionCall(
        call_id="livekit-call", name="track_order", arguments='{"order_id":"A1"}'
    )
    context = RunContext(
        session=SimpleNamespace(), speech_handle=SimpleNamespace(num_steps=1), function_call=call
    )
    positional, keywords = prepare_function_arguments(
        fnc=track,
        json_arguments=call.arguments,
        call_ctx=context,
    )
    bridge.commit_turn("livekit_test_eot")
    result = json.loads(await track(*positional, **keywords))
    assert result["order_id"] == "A1"
    assert bridge.history[0].current_call_id == "livekit-call"
