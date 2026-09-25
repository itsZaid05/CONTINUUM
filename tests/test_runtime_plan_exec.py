"""AgentRuntime plan execution: cancel/adopt, idempotency, retries, confirmation, modes."""

from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path
from typing import Any

from continuum.runtime import AgentRuntime, EventType, RuntimeEvent
from continuum.tools import FaultPlan, ToolManifest, load_manifests

TODAY = date(2026, 9, 25)
FAST = 0.02  # tool delays x0.02: a 1.5 s search takes 30 ms


def rt(**kw: Any) -> AgentRuntime:
    return AgentRuntime(tool_speed=FAST, today=TODAY, **kw)


async def say(r: AgentRuntime, text: str, sid: str = "s") -> None:
    await r.handle(RuntimeEvent(session_id=sid, type=EventType.TEXT, text=text))
    await r.handle(RuntimeEvent(session_id=sid, type=EventType.EOT))


def drain(r: AgentRuntime) -> list[Any]:
    out = []
    while not r.output_queue.empty():
        out.append(r.output_queue.get_nowait())
    return out


def events(r: AgentRuntime, name: str) -> list[dict[str, Any]]:
    return [t for t in r.trace if t["event"] == name]


def test_pivot_cancels_stale_call_and_redispatches_with_kept_constraint():
    async def go() -> None:
        r = rt()
        await say(r, "Find flights to Delhi tomorrow morning")
        first = next(a for a in drain(r) if a.type == "tool_call")
        await say(r, "Actually, Bangalore")
        acts = drain(r)
        assert any(a.type == "cancel" and a.call_id == first.call_id for a in acts)
        call = next(a for a in acts if a.type == "tool_call")
        assert call.args == {"to": "Bangalore", "date": "2026-09-26", "slot": "morning"}
        await asyncio.sleep(0.1)
        finals = [a for a in drain(r) if a.type == "final"]
        assert len(finals) == 1 and finals[0].snapshot.slots["to"] == "Bangalore"
        assert finals[0].snapshot.intent["goal"] == "search_flights"
        assert not [c for c in events(r, "tool_completed") if c["args"].get("to") == "Delhi"]

    asyncio.run(go())


def test_additive_turn_adopts_running_call_instead_of_restarting_it():
    async def go() -> None:
        r = rt()
        await say(r, "Find flights to Goa")
        await say(r, "also find a hotel there")
        assert not any(a.type == "cancel" for a in drain(r))
        assert len(events(r, "adopted")) == 1
        await asyncio.sleep(0.1)
        assert sorted(c["tool"] for c in events(r, "tool_completed")) == ["search_flights", "search_hotels"]

    asyncio.run(go())


def test_backchannel_leaves_work_untouched():
    async def go() -> None:
        r = rt()
        await say(r, "Find flights to Goa")
        await say(r, "hmm okay")
        acts = drain(r)
        assert not any(a.type == "cancel" for a in acts)
        assert sum(a.type == "tool_call" for a in acts) == 1

    asyncio.run(go())


def test_timeout_that_landed_is_verified_not_resent():
    async def go() -> None:
        r = rt(faults={"confirm_booking": FaultPlan(timeout_times=1, commit_on_timeout=True)})
        await say(r, "Book a flight to Goa tomorrow")
        await asyncio.sleep(0.25)
        effects = [e.tool for e in r.session("s").sandbox.effects]
        assert effects.count("confirm_booking") == 1
        assert events(r, "verify_after_timeout")[0]["committed"] is True
        assert not events(r, "retry")

    asyncio.run(go())


def test_blind_retry_ablation_double_books():
    async def go() -> None:
        r = rt(verify_timeouts=False, faults={"confirm_booking": FaultPlan(timeout_times=1, commit_on_timeout=True)})
        await say(r, "Book a flight to Goa tomorrow")
        await asyncio.sleep(0.3)
        assert [e.tool for e in r.session("s").sandbox.effects].count("confirm_booking") == 2

    asyncio.run(go())


def test_read_only_failures_are_retried_with_new_call_ids():
    async def go() -> None:
        r = rt(faults={"search_flights": FaultPlan(fail_times=2)}, retry_backoff_s=0.001)
        await say(r, "Find flights to Chennai")
        await asyncio.sleep(0.2)
        calls = [a for a in drain(r) if a.type == "tool_call"]
        assert len(calls) == 3 and len({c.call_id for c in calls}) == 3
        assert any(a for a in events(r, "final"))

    asyncio.run(go())


def test_mutating_failure_is_reported_not_retried():
    async def go() -> None:
        r = AgentRuntime(tool_speed=FAST, today=TODAY, faults={"create_ticket": FaultPlan(fail_times=1)})
        await r.handle(RuntimeEvent(session_id="s", type=EventType.MANIFEST,
                                    manifests=load_manifests(Path("data/manifests/support.json"))))
        await say(r, "Open a ticket: the printer is jammed")
        await asyncio.sleep(0.1)
        acts = drain(r)
        assert sum(a.type == "tool_call" for a in acts) == 1
        assert any(a.type == "speak" and "couldn't complete" in a.text for a in acts)

    asyncio.run(go())


def test_irreversible_step_waits_for_yes_and_is_dropped_on_no():
    async def run(answer: str) -> AgentRuntime:
        r = rt()
        await r.handle(RuntimeEvent(session_id="s", type=EventType.MANIFEST,
                                    manifests=load_manifests(Path("data/manifests/troubleshooting.json"))))
        await say(r, "can someone come and repair my WM-4500 on Monday?")
        assert any(a.type == "clarify" and "go ahead" in a.text for a in drain(r))
        await say(r, answer)
        await asyncio.sleep(0.1)
        return r

    yes = asyncio.run(run("yes please"))
    assert [c["tool"] for c in events(yes, "tool_completed")] == ["schedule_technician"]
    no = asyncio.run(run("no, not yet"))
    assert not events(no, "tool_completed")


def test_external_mode_uses_harness_results_and_drops_stale_ones():
    async def go() -> None:
        r = rt(tool_mode="external")
        await say(r, "Find flights to Delhi")
        first = next(a for a in drain(r) if a.type == "tool_call")
        await say(r, "Actually, Pune")
        second = next(a for a in drain(r) if a.type == "tool_call")
        await r.handle(RuntimeEvent(session_id="s", type=EventType.TOOL_RESULT, call_id=first.call_id,
                                    result={"flight_id": "FL-OLD"}))
        assert events(r, "tool_result_ignored")
        await r.handle(RuntimeEvent(session_id="s", type=EventType.TOOL_RESULT, call_id=second.call_id,
                                    result={"flight_id": "FL-1", "to": "Pune"}))
        await asyncio.sleep(0.01)
        final = next(a for a in drain(r) if a.type == "final")
        assert final.snapshot.slots["to"] == "Pune" and "FL-1" in final.text

    asyncio.run(go())


def test_manifests_are_session_scoped():
    async def go() -> None:
        r = rt()
        await r.handle(RuntimeEvent(session_id="a", type=EventType.MANIFEST, manifest=ToolManifest(name="secret_tool")))
        assert r.session("a").registry.has("secret_tool")
        assert not r.session("b").registry.has("secret_tool")
        assert not r.session("a").registry.has("search_flights")  # scenario tools replace built-ins

    asyncio.run(go())


def test_bad_event_does_not_stop_the_serve_loop():
    async def go() -> None:
        r = rt()
        await r.handle(RuntimeEvent(session_id="s", type=EventType.MANIFEST, manifest=ToolManifest(
            name="lookup_manual", keywords=["manual"],
            arguments={"type": "object", "properties": {"model": {"type": "string"}}, "required": ["model"]})))
        await say(r, "what does the manual say?")
        assert any(a.type == "clarify" for a in drain(r))  # missing model -> question, not ValueError
        await r.submit(RuntimeEvent(session_id="s", type=EventType.AUDIO, data=b"not a wav"))
        await r.run_once()  # perceive_audio raises; the loop must survive
        assert any(a.type == "clarify" for a in drain(r))
        assert events(r, "handler_error")

    asyncio.run(go())
