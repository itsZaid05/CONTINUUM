import asyncio

from continuum.runtime import AgentRuntime, EventType, RuntimeEvent
from continuum.streaming import StreamingSpeechState


def test_replacement_hypotheses_only_commit_the_last_candidate() -> None:
    state = StreamingSpeechState()
    state.update("find flights to Delhi", mode="replace", at_ms=1)
    state.update("find flights to Bangalore", mode="replace", at_ms=2)
    assert state.committed == []
    turn = state.commit(3)
    assert turn is not None and turn.text == "find flights to Bangalore"
    assert turn.candidate_revision == 2
    assert state.candidate == ""


def test_append_mode_preserves_jsonl_chunks() -> None:
    state = StreamingSpeechState()
    state.update("find flights ", mode="append", at_ms=1)
    state.update("to Goa", mode="append", at_ms=2)
    assert state.commit(3).text == "find flights to Goa"  # type: ignore[union-attr]


def test_partial_runtime_transcripts_cannot_dispatch_tools() -> None:
    async def check() -> None:
        runtime = AgentRuntime(tool_speed=0)
        await runtime.handle(
            RuntimeEvent(
                session_id="s",
                type=EventType.TEXT,
                text="Find flights to Delhi",
                text_mode="replace",
            )
        )
        assert runtime.output_queue.empty()
        assert runtime.trace[-1]["event"] == "speech_candidate"
        assert runtime.session("s").store.current() is None

        await runtime.handle(
            RuntimeEvent(
                session_id="s",
                type=EventType.TEXT,
                text="Find flights to Pune",
                text_mode="replace",
                is_final=True,
            )
        )
        assert any(row["event"] == "speech_committed" for row in runtime.trace)
        assert any(not runtime.output_queue.empty() for _ in [0])
        await runtime.close()

    asyncio.run(check())


def test_eot_after_final_transcript_does_not_commit_twice() -> None:
    async def check() -> None:
        runtime = AgentRuntime(tool_speed=0)
        await runtime.handle(
            RuntimeEvent(
                session_id="s",
                type=EventType.TEXT,
                text="Find flights to Pune",
                text_mode="replace",
                is_final=True,
            )
        )
        await runtime.handle(RuntimeEvent(session_id="s", type=EventType.EOT))
        committed = [row for row in runtime.trace if row["event"] == "speech_committed"]
        assert len(committed) == 1
        await runtime.close()

    asyncio.run(check())
