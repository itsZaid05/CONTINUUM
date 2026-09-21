from continuum.perception import perceive_text


def test_perception_fast_ack_latency():
    out = perceive_text("Actually, Bangalore", version_in=1)
    assert out.fast_ack_latency_ms < 200
    assert out.version_in == 1
    assert out.evidences[0].text == "Actually, Bangalore"


def test_perception_backchannel():
    out = perceive_text("Hmm, okay…")
    assert out.render_provenance["backchannel"] is True
    assert out.render_provenance["guessed_category"] == "noise"


def test_perception_retract_marker():
    out = perceive_text("Don't book it")
    assert out.render_provenance["guessed_category"] == "retract"


def test_perception_empty_raises():
    import pytest

    with pytest.raises(Exception):
        perceive_text("   ")


def test_perception_modify_guess():
    out = perceive_text("Actually, Bangalore", version_in=2)
    # Layer 0 should guess modify
    assert out.render_provenance["guessed_category"] == "modify"
