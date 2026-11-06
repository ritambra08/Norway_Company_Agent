#!/usr/bin/env python3
"""Rebuild the published site data (profiles.jsonl) from saved envelopes, without a new crawl.

    python3 scripts/rebuild_site_data.py submission/envelopes.jsonl.gz submission/profiles.jsonl

Only the display file is rewritten; the envelopes are read, never changed."""
import gzip
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from norway_agent import jsonl  # noqa: E402
from norway_agent.viewer import write_cards  # noqa: E402

src, out = Path(sys.argv[1]), Path(sys.argv[2])
text = gzip.open(src, "rt", encoding="utf-8").read() if src.suffix == ".gz" else src.read_text(encoding="utf-8")
envs = [__import__("json").loads(line) for line in jsonl.lines(text)]
write_cards(envs, out)
print(f"Wrote {len(envs)} profiles to {out}")
