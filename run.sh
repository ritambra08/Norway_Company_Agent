#!/usr/bin/env bash
# One command to run the agent:  ./run.sh [companies-file] [output-dir]
set -euo pipefail
cd "$(dirname "$0")"
exec python3 -m norway_agent run --input "${1:-companies.txt}" --out "${2:-out}"
