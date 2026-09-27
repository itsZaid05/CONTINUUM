import pytest

from continuum.tools import ToolManifest, ToolRegistry


def test_unseen_manifest_validates_and_binds_dynamic_schema() -> None:
    registry = ToolRegistry()
    registry.register(
        ToolManifest(
            name="weather",
            arguments={
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
                "additionalProperties": False,
            },
        )
    )
    assert registry.bind_args("weather", {"city": "Pune"}) == {"city": "Pune"}
    with pytest.raises(ValueError):
        registry.bind_args("weather", {})
    with pytest.raises(ValueError, match="is not of type 'string'"):
        registry.bind_args("weather", {"city": 7})


def test_invalid_json_schema_is_rejected_at_registration_boundary() -> None:
    with pytest.raises(ValueError, match="invalid JSON Schema"):
        ToolManifest(
            name="broken",
            arguments={"type": "object", "properties": {"value": {"type": "not-a-type"}}},
        )
