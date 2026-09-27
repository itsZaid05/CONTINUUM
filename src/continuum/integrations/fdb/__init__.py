"""Full-Duplex-Bench v3 integration.

The package keeps the framework-specific LiveKit/FDB edge separate from the
runtime kernel.  Importing it does not import LiveKit or require credentials;
LiveKit dependencies are loaded only by the agent/runner entry points.
"""

from .contracts import FdbBenchmark, FdbScenario, FdbToolCall, load_benchmark
from .manifests import FDB_TOOL_NAMES, fdb_registry, fdb_tool_manifests

__all__ = [
    "FDB_TOOL_NAMES",
    "FdbBenchmark",
    "FdbScenario",
    "FdbToolCall",
    "fdb_registry",
    "fdb_tool_manifests",
    "load_benchmark",
]
