"""Phase 2 — LLM adapter + dense gate + calibration.

Offline, deterministic, no network. Mocks HTTP.
"""

from unittest.mock import patch

import pytest

from continuum.contracts import ArbiterCategory
from continuum.delta_arbiter import ArbiterBackend, arbitrate_offline_fake
from continuum.llm import (
    build_fused_prompt,
    calibrate_confidence,
    call_gemini,
    call_ollama,
    call_openai,
    dense_classify,
    llm_parsed_to_decision,
    parse_structured_json,
    resolve_backend,
)
from continuum.versioned_state import VersionedStore


def _store():
    s = VersionedStore()
    s.create_initial({"destination": "Delhi", "goal_domain": "flights"})
    return s


# ---------------------------------------------------------------------------
# Prompt + calibration
# ---------------------------------------------------------------------------


def test_build_fused_prompt_contains_state_and_user():
    s = _store()
    p = build_fused_prompt("Actually, Bangalore", s.current())
    assert "Actually, Bangalore" in p
    assert "Delhi" in p
    assert "STRICT JSON" in p
    assert "FEW-SHOT" in p


def test_calibrate_confidence_conservative():
    # high conf for risky category should be nudged down with T=1.2
    raw = 0.94
    scaled = calibrate_confidence(raw, ArbiterCategory.RETRACT, temperature=1.2)
    assert scaled < raw
    assert 0.5 < scaled < 0.95
    # non-risky less penalized? but still scaled via logit
    scaled2 = calibrate_confidence(0.92, ArbiterCategory.MODIFY, temperature=1.2)
    assert scaled2 < 0.92
    # temperature 1.0 should be close to raw
    scaled3 = calibrate_confidence(0.90, ArbiterCategory.MODIFY, temperature=1.0)
    assert abs(scaled3 - 0.90) < 0.02
    # clamp
    assert 0 <= calibrate_confidence(0.0, ArbiterCategory.NOISE) <= 1
    assert 0 <= calibrate_confidence(1.0, ArbiterCategory.NOISE) <= 1


def test_parse_structured_json_variants():
    assert parse_structured_json('{"category":"MODIFY","confidence":0.9}')["category"] == "MODIFY"
    # markdown fence
    assert (
        parse_structured_json('```json\n{"category":"NOISE","confidence":0.95}\n```')["category"]
        == "NOISE"
    )
    # extra text
    assert (
        parse_structured_json('Here is JSON: {"category":"RETRACT","confidence":0.8} done')[
            "category"
        ]
        == "RETRACT"
    )
    # invalid
    assert parse_structured_json("not json at all") is None


def test_llm_parsed_to_decision_valid():
    parsed = {
        "category": "MODIFY",
        "confidence": 0.88,
        "delta": {
            "op": "replace",
            "field": "destination",
            "old_value": "Delhi",
            "new_value": "Bangalore",
            "span": "Actually, Bangalore",
        },
        "rationale": "test",
        "evidence_spans": [0],
    }
    d = llm_parsed_to_decision(
        parsed, raw_latency_ms=42, model="ollama:qwen3:4b", fallback_text="Actually, Bangalore"
    )
    assert d.category == ArbiterCategory.MODIFY
    assert d.delta.new_value == "Bangalore"
    assert d.model == "ollama:qwen3:4b"
    assert d.latency_ms == 42
    # calibration should have slightly lowered confidence
    assert d.confidence < 0.88
    assert d.confidence > 0.5


def test_llm_parsed_to_decision_noise_delta_none():
    parsed = {
        "category": "NOISE",
        "confidence": 0.96,
        "delta": {"op": "replace", "field": "destination", "new_value": "X"},
        "rationale": "noise",
    }
    d = llm_parsed_to_decision(parsed, 10, "gemini:flash", "Hmm, okay…")
    assert d.category == ArbiterCategory.NOISE
    assert d.delta is None


def test_llm_parsed_to_decision_invalid_fallback():
    d = llm_parsed_to_decision(None, 15, "openai:gpt-4o-mini", "blah random not pattern Hey there")
    # should fallback to MODIFY heuristic with low confidence
    assert d.category == ArbiterCategory.MODIFY
    assert d.confidence < 0.65


def test_resolve_backend_aliases():
    assert resolve_backend("fake") == "offline-fake"
    assert resolve_backend("offline") == "offline-fake"
    assert resolve_backend("gpt") == "openai"
    assert resolve_backend("minilm") == "dense"
    assert resolve_backend("GEMINI") == "gemini"
    assert resolve_backend(None) == "offline-fake"
    # env var
    with patch.dict("os.environ", {"CONTINUUM_BACKEND": "dense"}):
        assert resolve_backend(None) == "dense"
    with patch.dict("os.environ", {"ARBITER_BACKEND": "openai"}, clear=False):
        # CONTINUUM_BACKEND not set, picks ARBITER_BACKEND
        import os

        os.environ.pop("CONTINUUM_BACKEND", None)
        assert resolve_backend(None) == "openai"


# ---------------------------------------------------------------------------
# Dense gate
# ---------------------------------------------------------------------------


def test_dense_disable_fallback():
    with patch.dict("os.environ", {"CONTINUUM_DENSE_DISABLE": "1"}):
        # should return None quickly, no download
        assert dense_classify("Actually, Bangalore") is None


def test_dense_backend_fallback_offline_fake():
    # dense with disable should still produce valid decision via offline-fake fallback but tagged
    with patch.dict("os.environ", {"CONTINUUM_DENSE_DISABLE": "1"}):
        b = ArbiterBackend("dense")
        d = b.arbitrate("Actually, Bangalore", _store().current())
        assert d.category == ArbiterCategory.MODIFY
        assert "fallback" in d.model
        assert d.latency_ms >= 5


def test_dense_local_files_only_no_download():
    # default (no DOWNLOAD flag) should not hang, returns fallback quickly
    # patch env to ensure no download attempted
    import os

    os.environ.pop("CONTINUUM_DENSE_DOWNLOAD", None)
    # ensure disable not set
    os.environ.pop("CONTINUUM_DENSE_DISABLE", None)
    # call dense classify — should return None if model not cached (fast)
    # we don't assert on download, just that it doesn't hang >10s and returns either valid or None
    import time

    t0 = time.perf_counter()
    res = dense_classify("Hmm, okay…")
    elapsed = time.perf_counter() - t0
    assert elapsed < 15.0  # fail fast (model load ~4-10s cold on slower hardware, but not 60s download)
    # res may be None (most CI) or valid category; both ok
    if res is not None:
        cat, conf = res
        assert isinstance(cat, ArbiterCategory)
        assert 0 <= conf <= 1


# ---------------------------------------------------------------------------
# HTTP backends — mock _httpx_post to avoid network
# ---------------------------------------------------------------------------


def test_call_ollama_mock_success():
    fake_prompt = "test"
    with patch(
        "continuum.llm._httpx_post",
        return_value=(
            200,
            '{"message":{"content":"{\\"category\\":\\"MODIFY\\", \\"confidence\\":0.9, \\"delta\\":{\\"op\\":\\"replace\\",\\"field\\":\\"destination\\",\\"new_value\\":\\"Bangalore\\"}, \\"rationale\\":\\"ok\\"}"}}',
        ),
    ):
        raw, lat = call_ollama(fake_prompt, model="qwen3:4b")
        assert raw is not None
        assert "MODIFY" in raw
        assert isinstance(lat, int)


def test_call_ollama_failure_returns_none():
    with patch("continuum.llm._httpx_post", return_value=(500, "error")):
        raw, lat = call_ollama("hi")
        assert raw is None
        assert lat >= 0


def test_call_gemini_no_key_returns_none():
    with patch.dict("os.environ", {"GEMINI_API_KEY": "", "GOOGLE_API_KEY": ""}, clear=False):
        import os

        os.environ.pop("GEMINI_API_KEY", None)
        os.environ.pop("GOOGLE_API_KEY", None)
        raw, lat = call_gemini("hi")
        assert raw is None
        assert lat == 0


def test_call_gemini_mock_success():
    with (
        patch.dict("os.environ", {"GEMINI_API_KEY": "fake-key"}),
        patch(
            "continuum.llm._httpx_post",
            return_value=(
                200,
                '{"candidates":[{"content":{"parts":[{"text":"{\\"category\\":\\"RETRACT\\",\\"confidence\\":0.92,\\"delta\\":{\\"op\\":\\"remove\\",\\"field\\":\\"booking_instruction\\"},\\"rationale\\":\\"x\\"}"}]}}]}',
            ),
        ),
    ):
        raw, lat = call_gemini("hi")
        assert raw is not None
        assert "RETRACT" in raw


def test_call_openai_no_key_returns_none():
    with patch.dict("os.environ", {"OPENAI_API_KEY": ""}, clear=False):
        import os

        os.environ.pop("OPENAI_API_KEY", None)
        raw, lat = call_openai("hi")
        assert raw is None
        assert lat == 0


def test_call_openai_mock_success():
    with (
        patch.dict("os.environ", {"OPENAI_API_KEY": "sk-fake"}),
        patch(
            "continuum.llm._httpx_post",
            return_value=(
                200,
                '{"choices":[{"message":{"content":"{\\"category\\":\\"NOISE\\",\\"confidence\\":0.95,\\"rationale\\":\\"x\\"}"}}]}',
            ),
        ),
    ):
        raw, lat = call_openai("hi")
        assert raw is not None
        assert "NOISE" in raw


# ---------------------------------------------------------------------------
# ArbiterBackend integration — fallback paths
# ---------------------------------------------------------------------------


def test_backend_ollama_fallback_offline():
    # No ollama server running -> fallback to offline-fake with offset
    b = ArbiterBackend("ollama")
    # patch call_ollama to simulate failure quickly
    with patch("continuum.llm.call_ollama", return_value=(None, 12)):
        d = b.arbitrate("Actually, Bangalore", _store().current())
        assert d.category == ArbiterCategory.MODIFY
        assert "fallback from ollama" in d.model
        assert d.latency_ms >= 400  # offset + base


def test_backend_gemini_fallback_offline():
    b = ArbiterBackend("gemini")
    with patch("continuum.llm.call_gemini", return_value=(None, 0)):
        d = b.arbitrate("Don't book it", _store().current())
        assert d.category == ArbiterCategory.RETRACT
        assert "fallback from gemini" in d.model
        assert d.latency_ms >= 650


def test_backend_openai_fallback_offline():
    b = ArbiterBackend("openai")
    with patch("continuum.llm.call_openai", return_value=(None, 0)):
        d = b.arbitrate("Forget flights, find trains", _store().current())
        assert d.category == ArbiterCategory.NEW_GOAL
        assert "fallback from openai" in d.model
        assert d.latency_ms >= 500


def test_backend_ollama_success_parses_llm_json():
    b = ArbiterBackend("ollama")
    fake_llm_json = '{"category":"ADD_CONSTRAINT","confidence":0.85,"delta":{"op":"add","field":"constraints","new_value":{"time":"morning"},"span":"keep morning"},"rationale":"llm says","evidence_spans":[0]}'
    with patch("continuum.llm.call_ollama", return_value=(fake_llm_json, 320)):
        d = b.arbitrate("keep morning", _store().current())
        assert d.category == ArbiterCategory.ADD_CONSTRAINT
        assert d.model.startswith("ollama:")
        assert d.latency_ms == 320
        # calibrated slightly lower
        assert d.confidence < 0.85


def test_backend_unknown_raises():
    with pytest.raises(ValueError):
        ArbiterBackend("unknown-backend-xyz")


def test_backend_alias_gpt_maps_to_openai():
    b = ArbiterBackend("gpt")
    assert b.name == "openai"


def test_arbitrate_convenience_respects_env():
    with patch.dict("os.environ", {"CONTINUUM_BACKEND": "dense", "CONTINUUM_DENSE_DISABLE": "1"}):
        # convenience arbitrate with default should pick env backend (dense fallback)
        from continuum.delta_arbiter import arbitrate

        d = arbitrate("Actually, Bangalore", _store().current())
        assert "fallback" in d.model or "dense" in d.model


def test_offline_fake_still_deterministic():
    s = _store()
    d1 = arbitrate_offline_fake("Actually, Bangalore", s.current())
    d2 = ArbiterBackend("offline-fake").arbitrate("Actually, Bangalore", s.current())
    assert d1.category == d2.category == ArbiterCategory.MODIFY
    assert d1.delta.new_value == d2.delta.new_value == "Bangalore"
