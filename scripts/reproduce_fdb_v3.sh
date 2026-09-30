#!/usr/bin/env bash
# Reproduce CONTINUUM's official FDB-v3 evaluation path with the unmodified
# upstream inference/evaluation scripts. Benchmark recordings are distributed
# separately by FDB and must be supplied as the first argument.
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
usage: scripts/reproduce_fdb_v3.sh DATA_DIR [--force] [--skip-sync]

DATA_DIR is the extracted fdb_v3_data_released directory from the official FDB
release. This command installs the locked FDB environment, pins/audits upstream,
starts CONTINUUM's LiveKit worker, runs all 100 recordings, and runs the three
upstream evaluations with their LLM judge enabled.

Required environment variables:
  LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET, GOOGLE_API_KEY, OPENAI_API_KEY

Optional:
  FDB_SOURCE_DIR   upstream checkout (default: .artifacts/Full-Duplex-Bench)
  FDB_RUN_DIR      output directory (default: reports/fdb-v3/<UTC timestamp>)
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi
[[ $# -ge 1 ]] || { usage; exit 2; }
DATA_DIR="$1"
shift
FORCE=0
SKIP_SYNC=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --force) FORCE=1 ;;
    --skip-sync) SKIP_SYNC=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage; exit 2 ;;
  esac
  shift
done

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
DATA_DIR="$(cd "$DATA_DIR" && pwd)"
SOURCE_DIR="${FDB_SOURCE_DIR:-$ROOT/.artifacts/Full-Duplex-Bench}"
PROVIDER="gemini2_5"
RUN_DIR="${FDB_RUN_DIR:-$ROOT/reports/fdb-v3/$(date -u +%Y%m%dT%H%M%SZ)}"

for name in LIVEKIT_URL LIVEKIT_API_KEY LIVEKIT_API_SECRET GOOGLE_API_KEY OPENAI_API_KEY; do
  [[ -n "${!name:-}" ]] || {
    echo "missing required environment variable: $name" >&2
    exit 2
  }
done
[[ -d "$DATA_DIR" ]] || { echo "FDB data directory not found: $DATA_DIR" >&2; exit 2; }
command -v ffmpeg >/dev/null 2>&1 || {
  echo "ffmpeg is required by the upstream FDB runner" >&2
  exit 2
}

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required; install it first: https://docs.astral.sh/uv/" >&2
  exit 2
fi
if [[ "$SKIP_SYNC" -eq 0 ]]; then
  uv sync --frozen --extra dev --extra fdb --extra fdb-eval
fi

scripts/fetch_fdb.sh
uv run --frozen python scripts/audit_fdb_contract.py --source-dir "$SOURCE_DIR"

mkdir -p "$RUN_DIR"
export CONTINUUM_TELEMETRY_PATH="$RUN_DIR/agent-telemetry.jsonl"
# The unmodified upstream runner reads these two fixed paths.
export FDB_TOOL_LOG_PATH="/tmp/agent_tool_calls.log"
: > "$FDB_TOOL_LOG_PATH"
: > /tmp/agent_heartbeat.log

agent_log="$RUN_DIR/agent-worker.log"
uv run --frozen continuum-fdb-agent --latency normal start >"$agent_log" 2>&1 &
AGENT_PID=$!
cleanup() {
  if kill -0 "$AGENT_PID" 2>/dev/null; then
    kill "$AGENT_PID" 2>/dev/null || true
    wait "$AGENT_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

# The LiveKit worker has no local listening socket. A short process-liveness
# check catches invalid credentials or import errors before batch inference.
sleep 3
if ! kill -0 "$AGENT_PID" 2>/dev/null; then
  echo "CONTINUUM worker exited during startup; see $agent_log" >&2
  cat "$agent_log" >&2 || true
  exit 1
fi

RUN_ARGS=()
[[ "$FORCE" -eq 1 ]] && RUN_ARGS+=(--force)
uv run --frozen python "$SOURCE_DIR/v3/run_tool_benchmark_all_released.py" \
  --provider "$PROVIDER" \
  --root_dir "$DATA_DIR" \
  "${RUN_ARGS[@]}"

# Keep machine-readable official reports beside the worker logs. The results
# themselves remain in the user-supplied FDB data directory, exactly as the
# upstream evaluator expects.
uv run --frozen python "$SOURCE_DIR/v3/evaluate_tool_calls.py" \
  --benchmark "$SOURCE_DIR/v3/benchmark_data_v2.json" \
  --results-dir "$DATA_DIR" \
  --provider "$PROVIDER" \
  --output "$RUN_DIR/tool_accuracy.json" \
  --use-llm
uv run --frozen python "$SOURCE_DIR/v3/evaluate_pass_rate.py" \
  --benchmark "$SOURCE_DIR/v3/benchmark_data_v2.json" \
  --results-dir "$DATA_DIR" \
  --provider "$PROVIDER" \
  --output "$RUN_DIR/pass_rate.json" \
  --use-llm
uv run --frozen python "$SOURCE_DIR/v3/analyze_tool_latency.py" \
  --results-dir "$DATA_DIR" \
  --provider "$PROVIDER" \
  --output "$RUN_DIR/latency.json"

cp "$FDB_TOOL_LOG_PATH" "$RUN_DIR/agent-tool-calls.jsonl"
printf 'FDB-v3 evaluation complete. Reports and sanitized worker logs: %s\n' "$RUN_DIR"
