#!/usr/bin/env bash
# Official-size timing test: one run on 1,000 fresh companies (a different seed from the smoke test),
# then self-validation and a one-screen summary. Writes only to scale/ (git-ignored); it never touches
# submission/.
#   ./scripts/scale_test.sh            # 1,000 companies
#   N=1100 ./scripts/scale_test.sh     # the size the official batch may grow to
set -euo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$DIR"
N="${N:-1000}"
SEED="${SEED:-20261106}"
UNIVERSE="${UNIVERSE:-signalpost-universe.jsonl.gz}"
if [ ! -f "$UNIVERSE" ]; then
  curl -fL 'https://builderr.ai/signalpost-company-universe-2025.jsonl.gz' -o "$UNIVERSE"
fi
rm -rf scale
python3 scripts/select_entry_batch.py --universe "$UNIVERSE" --count "$N" --seed "$SEED" \
  --output scale/companies.jsonl
python3 -m norway_agent run --organisations scale/companies.jsonl --output scale/envelopes.jsonl \
  --report scale/run-report.json --run-id "scale-$(date -u +%Y%m%dT%H%M%SZ)" --expected-count "$N"
python3 -m norway_agent validate --output scale/envelopes.jsonl --organisations scale/companies.jsonl \
  > scale/validate.txt
python3 - <<'PY'
import json
r = json.load(open("scale/run-report.json"))
v = open("scale/validate.txt").read().strip().splitlines()[-1]
fam = r["coverage_by_family"]
print()
print("=== SCALE TEST SUMMARY (send this to Claude) ===")
print(f"version {r['version']} | companies {r['expected_count']} | envelopes {r['envelopes_written']} "
      f"| one per input {r['one_terminal_envelope_per_input']}")
print(f"runtime {r['runtime_seconds'] / 60:.1f} min | latency p50 {r['per_company_latency_ms']['p50'] / 1000:.1f}s "
      f"p95 {r['per_company_latency_ms']['p95'] / 1000:.1f}s")
print(f"requests {r['requests']['total']} of {r['requests']['budget']} | by source {r['requests']['by_source']} "
      f"| retried {r['requests']['retried_errors']}")
print(f"stage errors {r['stage_errors'] or 'none'}")
print(f"websites {r['sources']['websites']}")
nav = r["sources"]["nav_jobs"]
print(f"nav active ads {nav.get('active_ads')} | candidates {nav.get('candidate_ads')} "
      f"| rejected other orgnr {nav.get('rejected_other_orgnr')} | error {nav.get('error')}")
print("coverage " + ", ".join(f"{k} {v['share']:.0%}" for k, v in sorted(fam.items())))
print(f"validate: {v}")
PY
