#!/usr/bin/env bash
# 100-company smoke test on the official public universe, then self-validation.
# Writes the submission artefacts to submission/ (commit them: the brief requires a smoke-test result).
#   ./scripts/smoke_test.sh            # 100 companies
#   N=1000 ./scripts/smoke_test.sh     # larger local test (not scored)
set -euo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$DIR"
N="${N:-100}"
UNIVERSE="${UNIVERSE:-signalpost-universe.jsonl.gz}"
if [ ! -f "$UNIVERSE" ]; then
  curl -fL 'https://builderr.ai/signalpost-company-universe-2025.jsonl.gz' -o "$UNIVERSE"
fi
python3 scripts/select_entry_batch.py --universe "$UNIVERSE" --count "$N" --output "smoke/smoke-companies.jsonl"
RUN_ID="smoke-$(date -u +%Y%m%dT%H%M%SZ)"
python3 -m norway_agent run --organisations smoke/smoke-companies.jsonl --output smoke/envelopes.jsonl \
  --report smoke/run-report.json --run-id "$RUN_ID" --expected-count "$N"
python3 -m norway_agent validate --output smoke/envelopes.jsonl --organisations smoke/smoke-companies.jsonl \
  | tee smoke/validate.txt
# Second run on the same batch: proves refresh + change detection (previous = first run).
python3 -m norway_agent run --organisations smoke/smoke-companies.jsonl --output smoke/envelopes.jsonl \
  --report smoke/run-report-refresh.json --run-id "${RUN_ID}-refresh" --expected-count "$N" --no-viewer
python3 -m norway_agent validate --output smoke/envelopes.jsonl --organisations smoke/smoke-companies.jsonl \
  | tee smoke/validate-refresh.txt
mkdir -p submission
cp smoke/smoke-companies.jsonl smoke/run-report.json smoke/run-report-refresh.json smoke/validate.txt \
   smoke/validate-refresh.txt smoke/envelopes.jsonl smoke/profiles.jsonl smoke/index.html submission/
gzip -kf submission/envelopes.jsonl && rm submission/envelopes.jsonl
echo "Smoke test done. Commit submission/ (envelopes.jsonl.gz, run reports, validate output, index.html)."
