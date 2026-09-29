#!/usr/bin/env bash
set -euo pipefail

# Fetch the exact FDB-v3 source revision audited by CONTINUUM. Benchmark audio
# is distributed separately by the upstream project and is intentionally not
# committed to this repository.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="${FDB_SOURCE_DIR:-$ROOT/.artifacts/Full-Duplex-Bench}"
URL="https://github.com/DanielLin94144/Full-Duplex-Bench.git"
REV="3e799c45a045256f47d5f1c9cda90157e2d2ec9e"

if [[ -e "$DEST/.git" ]]; then
  git -C "$DEST" fetch --depth 1 origin "$REV"
else
  mkdir -p "$(dirname "$DEST")"
  git clone --filter=blob:none --no-checkout "$URL" "$DEST"
  git -C "$DEST" fetch --depth 1 origin "$REV"
fi

git -C "$DEST" checkout --detach "$REV"
actual="$(git -C "$DEST" rev-parse HEAD)"
[[ "$actual" == "$REV" ]] || { echo "FDB revision mismatch: $actual" >&2; exit 1; }

echo "FDB-v3 source ready: $DEST ($actual)"
echo "Data instructions: $DEST/v3/README.md"
