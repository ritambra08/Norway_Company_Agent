#!/usr/bin/env python3
"""Draw a reproducible random sample of org numbers from the official bulk file.

One request: Enhetsregisteret's full CSV download (open data, no key).
Usage:  python3 scripts/sample_orgnrs.py --n 1000 --seed 17 > companies.txt

NOTE: written against the documented download endpoint but not run in the
authoring sandbox (it could not reach brreg.no). Run it once and eyeball the
first lines of the output.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import io
import random
import sys
import urllib.request

URL = "https://data.brreg.no/enhetsregisteret/api/enheter/lastned/csv"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=17)
    ap.add_argument("--only-active", action="store_true", help="skip bankrupt / liquidating entities")
    a = ap.parse_args()

    req = urllib.request.Request(URL, headers={"User-Agent": "norway-company-agent/0.1 (sampler)"})
    rng = random.Random(a.seed)
    sample: list[str] = []
    seen = 0
    with urllib.request.urlopen(req, timeout=300) as resp:
        raw = resp.read()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
    col = next((c for c in (reader.fieldnames or []) if c.lower() in ("organisasjonsnummer", "orgnr")), None)
    if col is None:
        print(f"No org number column in header: {reader.fieldnames}", file=sys.stderr)
        return 1
    for row in reader:
        if a.only_active and (row.get("konkurs", "").lower() == "true" or row.get("underAvvikling", "").lower() == "true"):
            continue
        o = (row.get(col) or "").strip()
        if len(o) != 9 or not o.isdigit():
            continue
        seen += 1
        if len(sample) < a.n:  # reservoir sampling
            sample.append(o)
        else:
            j = rng.randrange(seen)
            if j < a.n:
                sample[j] = o
    print(f"sampled {len(sample)} of {seen} entities", file=sys.stderr)
    print("\n".join(sample))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
