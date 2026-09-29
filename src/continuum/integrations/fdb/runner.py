"""CLI entrypoint for the FDB-managed LiveKit agent worker."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Sequence

from .config import FdbAgentConfig
from .provider import selected_provider


def _consume_option(argv: list[str], name: str) -> str | None:
    if name not in argv:
        return None
    index = argv.index(name)
    if index + 1 >= len(argv):
        raise SystemExit(f"{name} requires a value")
    value = argv[index + 1]
    del argv[index : index + 2]
    return value


def main(argv: Sequence[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    latency = _consume_option(args, "--latency")
    if latency is not None:
        os.environ["FDB_LATENCY_PROFILE"] = latency
    check_only = "--check" in args
    if check_only:
        args.remove("--check")

    config = FdbAgentConfig.from_env()
    # Console mode does not need a LiveKit Cloud project.  FDB's managed room
    # workflow uses start/dev and therefore gets an explicit, early check.
    if "console" not in args:
        config.validate_livekit_environment()
    provider = selected_provider(config.provider)
    provider.validate_environment()

    if check_only:
        print(
            json.dumps(
                {
                    "ready": True,
                    "provider": provider.name,
                    "model": config.model,
                    "latency_profile": config.latency_profile,
                    "credentials": "present (values not displayed)",
                },
                sort_keys=True,
            )
        )
        return 0

    try:
        from livekit import agents
    except ImportError as exc:  # pragma: no cover - optional dependency path
        raise SystemExit("LiveKit is not installed; sync with the 'fdb' extra") from exc
    from .agent import server

    sys.argv = [sys.argv[0], *args]
    agents.cli.run_app(server)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
