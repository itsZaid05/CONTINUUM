"""Full-Duplex-Bench v3 integration.

The package keeps the framework-specific LiveKit/FDB edge separate from the
runtime kernel.  Importing it does not import LiveKit or require credentials;
LiveKit dependencies are loaded only by the agent/runner entry points.
"""

from .backend import FdbBackend, FdbMockBackend
from .contracts import FdbBenchmark, FdbScenario, FdbToolCall, load_benchmark
from .manifests import FDB_TOOL_NAMES, fdb_registry, fdb_tool_manifests
from .provider import GeminiNativeAudioProvider, NativeAudioConfig, NativeAudioProvider
from .tool_bridge import FdbToolBridge

__all__ = [
    "FDB_TOOL_NAMES",
    "FdbBackend",
    "FdbBenchmark",
    "FdbMockBackend",
    "FdbScenario",
    "FdbToolBridge",
    "FdbToolCall",
    "GeminiNativeAudioProvider",
    "NativeAudioConfig",
    "NativeAudioProvider",
    "fdb_registry",
    "fdb_tool_manifests",
    "load_benchmark",
]
