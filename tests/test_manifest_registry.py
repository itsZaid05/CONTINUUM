import pytest

from continuum.tools import ToolManifest, ToolRegistry


def test_unseen_manifest_validates_and_binds_dynamic_schema() -> None:
    registry = ToolRegistry()
    registry.register(ToolManifest(name="weather", arguments={"type": "object", "properties": {"city": {}}, "required": ["city"]}))
    assert registry.bind_args("weather", {"city": "Pune"}) == {"city": "Pune"}
    with pytest.raises(ValueError):
        registry.bind_args("weather", {})
