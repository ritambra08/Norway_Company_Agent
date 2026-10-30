"""Reading the company batch: JSONL (official format), JSON, CSV or plain text."""

from __future__ import annotations

import gzip
import json
import re
from pathlib import Path

from . import orgnr as orgnr_mod

_KEYS = ("organisation_number", "organization_number", "organisasjonsnummer", "orgnr", "org_nr",
         "org_number", "orgNummer", "organisationNumber", "id")
_NUMBER = re.compile(r"(?<!\d)(\d{3}\s?\d{3}\s?\d{3})(?!\d)")


def _from_obj(item) -> str:
    if isinstance(item, dict):
        for k in _KEYS:
            if k in item and item[k] not in (None, ""):
                return str(item[k])
        for v in item.values():  # last resort: the first value that looks like an org number
            if isinstance(v, (str, int)) and _NUMBER.fullmatch(str(v).strip()):
                return str(v)
        return json.dumps(item, ensure_ascii=False)[:60]
    return str(item)


def read_batch(path: str) -> tuple[list[str], list[str]]:
    """Return (unique inputs in order, duplicates dropped). Inputs are normalised org numbers when
    they look like one; anything else is kept verbatim so it gets an explicit failed envelope."""
    p = Path(path)
    raw = gzip.open(p, "rb").read() if p.suffix == ".gz" else p.read_bytes()
    text = raw.decode("utf-8-sig")
    found: list[str] = []
    stripped = text.lstrip()
    if stripped[:1] in ("[", "{"):
        try:
            data = json.loads(text)
            data = data if isinstance(data, list) else [data]
        except ValueError:
            data = [json.loads(line) for line in text.splitlines() if line.strip()]
        found = [_from_obj(x) for x in data]
    else:
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            m = _NUMBER.search(line)
            if m:
                found.append(m.group(1))
            elif i == 0 and re.search(r"[A-Za-z]", line):
                continue  # CSV header
            else:
                found.append(line.strip())
    norm = [orgnr_mod.normalize(x) or x for x in found]
    seen: set[str] = set()
    unique, dups = [], []
    for x in norm:
        if x in seen:
            dups.append(x)
        else:
            seen.add(x)
            unique.append(x)
    return unique, dups
