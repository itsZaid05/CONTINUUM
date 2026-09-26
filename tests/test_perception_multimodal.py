import io
import wave

from continuum.perception import perceive_audio, perceive_frame


def _wav() -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as writer:
        writer.setnchannels(1); writer.setsampwidth(2); writer.setframerate(8000); writer.writeframes(b"\0\0" * 8)
    return out.getvalue()


def test_raw_wav_is_not_authoritative() -> None:
    assert "ambiguous" in perceive_audio(_wav()).render_provenance


def test_ungrounded_png_requires_clarification() -> None:
    result = perceive_frame(b"\x89PNG\r\n\x1a\n")
    assert "ambiguous" in result.render_provenance
