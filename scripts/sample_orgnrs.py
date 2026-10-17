#!/usr/bin/env python3
"""Draw a reproducible random sample of org numbers from the official bulk file.

One request: Enhetsregisteret's full CSV download (open data, no key). The file is
streamed row by row, so memory use stays small whatever its size.

Usage:
  python3 scripts/sample_orgnrs.py --n 1000 --seed 17 --out companies.txt
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
    ap.add_argument("--out", help="write to this file (UTF-8) instead of stdout")
    a = ap.parse_args()

    req = urllib.request.Request(URL, headers={"User-Agent": "norway-company-agent/0.1 (sampler)"})
    rng = random.Random(a.seed)
    sample: list[str] = []
    seen = 0
    with urllib.request.urlopen(req, timeout=300) as resp:
        gz = resp.peek(2)[:2] == b"\x1f\x8b"
        stream = gzip.GzipFile(fileobj=resp) if gz else resp
        reader = csv.DictReader(io.TextIOWrapper(stream, encoding="utf-8-sig", newline=""))
        col = next((c for c in (reader.fieldnames or []) if c.lower() in ("organisasjonsnummer", "orgnr")), None)
        if col is None:
            print(f"No org number column in header: {reader.fieldnames}", file=sys.stderr)
            return 1
        for row in reader:
            if a.only_active and (row.get("konkurs", "").lower() == "true"
                                  or row.get("underAvvikling", "").lower() == "true"):
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
    text = "\n".join(sample) + "\n"
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
