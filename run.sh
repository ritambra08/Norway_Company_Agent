#!/usr/bin/env bash
# The one evaluator command:
#   ./run.sh <batch.jsonl> [out-dir] [run-id]
# Writes <out-dir>/envelopes.jsonl (one terminal envelope per input), run-report.json,
# profiles.jsonl, index.html and snapshots/. If <out-dir>/envelopes.jsonl already exists it is
# used as the previous snapshot, so a rerun reports material changes.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
BATCH="${1:?usage: ./run.sh <batch.jsonl> [out-dir] [run-id]}"
OUT="${2:-out}"
RUN_ID="${3:-run-$(date -u +%Y%m%dT%H%M%SZ)}"
exec env PYTHONPATH="$DIR${PYTHONPATH:+:$PYTHONPATH}" python3 -m norway_agent run \
  --organisations "$BATCH" --output "$OUT/envelopes.jsonl" --report "$OUT/run-report.json" --run-id "$RUN_ID"
