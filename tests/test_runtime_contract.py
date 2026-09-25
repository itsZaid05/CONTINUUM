import asyncio

from continuum.runtime import AgentRuntime, EventType, RuntimeEvent
from continuum.tools import ToolManifest, ToolRegistry


def test_two_queue_eot_emits_call_and_final() -> None:
    async def check() -> None:
        registry = ToolRegistry(
            [ToolManifest(name="lookup", arguments={"type": "object"}, delay_s=0)]
        )
        runtime = AgentRuntime(registry=registry)
        await runtime.handle(RuntimeEvent(session_id="a", type=EventType.TEXT, text="find a hotel"))
        await runtime.handle(RuntimeEvent(session_id="a", type=EventType.EOT))
        actions = []
        while not runtime.output_queue.empty():
            actions.append(await runtime.output_queue.get())
        assert any(action.type == "tool_call" and action.call_id for action in actions)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert (await runtime.output_queue.get()).type == "final"

    asyncio.run(check())


def test_interrupt_cancels_and_rejects_late_result() -> None:
    async def check() -> None:
        registry = ToolRegistry(
            [ToolManifest(name="slow", arguments={"type": "object"}, delay_s=1)]
        )
        runtime = AgentRuntime(registry=registry)
        await runtime.handle(RuntimeEvent(session_id="a", type=EventType.TEXT, text="find options"))
        await runtime.handle(RuntimeEvent(session_id="a", type=EventType.EOT))
        actions = []
        while not runtime.output_queue.empty():
            actions.append(await runtime.output_queue.get())
        call_id = next(action.call_id for action in actions if action.type == "tool_call")
        await runtime.handle(
            RuntimeEvent(session_id="a", type=EventType.INTERRUPT, call_id=call_id)
        )
        assert (await runtime.output_queue.get()).type == "cancel"
        await asyncio.sleep(0)
        assert runtime.output_queue.empty()

    asyncio.run(check())


def test_sessions_are_isolated() -> None:
    async def check() -> None:
        runtime = AgentRuntime()
        await runtime.handle(
            RuntimeEvent(session_id="one", type=EventType.TEXT, text="find flights")
        )
        await runtime.handle(
            RuntimeEvent(session_id="two", type=EventType.TEXT, text="find hotels")
        )
        assert runtime.session("one").text != runtime.session("two").text

    asyncio.run(check())
