"""Small compatibility helpers for the supported Python 3.10--3.12 range."""

from datetime import timezone
from enum import Enum

# ``datetime.UTC`` was introduced in 3.11.  Keeping the alias here avoids
# importing a symbol that does not exist on the oldest supported interpreter.
UTC = timezone.utc


class StrEnum(str, Enum):
    """Backport of :class:`enum.StrEnum` sufficient for wire enums."""
