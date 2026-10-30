#!/usr/bin/env python3
"""Starter-kit compatible entry point (same flags as the Builderr reference agent).

    python3 scripts/run_competition_batch.py --organisations smoke-companies.jsonl \
        --output out/smoke-envelopes.jsonl --report out/smoke-report.json \
        --profiles-output out/smoke-profiles.jsonl --run-id smoke-001 --expected-count 100

``--bulk`` is accepted and ignored: identity is fetched live in batches of 100, so no registry
download is needed.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from norway_agent.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(["run", *sys.argv[1:]]))
