#!/usr/bin/env bash
set -euo pipefail

PINNED_REVISION="3e799c45a045256f47d5f1c9cda90157e2d2ec9e"
SOURCE_DIR="${FDB_SOURCE_DIR:-.artifacts/Full-Duplex-Bench}"

if [[ $# -lt 1 ]]; then
  echo "usage: $0 DATA_DIR [upstream run_tool_benchmark_all_released.py options...]" >&2
  exit 2
fi
DATA_DIR="$1"
shift

for name in LIVEKIT_URL LIVEKIT_API_KEY LIVEKIT_API_SECRET; do
  if [[ -z "${!name:-}" ]]; then
    echo "missing required environment variable: ${name}" >&2
    exit 2
  fi
done

if [[ ! -d "${SOURCE_DIR}/.git" ]]; then
  echo "missing upstream checkout at ${SOURCE_DIR}; run scripts/fetch_fdb.sh first" >&2
  exit 2
fi
revision="$(git -C "${SOURCE_DIR}" rev-parse HEAD)"
if [[ "${revision}" != "${PINNED_REVISION}" ]]; then
  echo "upstream revision mismatch: expected ${PINNED_REVISION}, found ${revision}" >&2
  exit 2
fi
if [[ ! -d "${DATA_DIR}" ]]; then
  echo "FDB released data directory not found: ${DATA_DIR}" >&2
  exit 2
fi
if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "ffmpeg is required by the official FDB runner" >&2
  exit 2
fi

# Keep upstream inference logic unmodified. The selected provider name is the
# exact identifier expected by the pinned FDB-v3 scripts.
exec python "${SOURCE_DIR}/v3/run_tool_benchmark_all_released.py" \
  --provider gemini2_5 \
  --root_dir "${DATA_DIR}" \
  "$@"
