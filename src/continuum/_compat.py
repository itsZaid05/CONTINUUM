"""Small compatibility helpers for the supported Python 3.10--3.12 range."""
from enum import Enum


class StrEnum(str, Enum):
    """Backport of :class:`enum.StrEnum` sufficient for wire enums."""
