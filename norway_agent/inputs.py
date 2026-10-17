"""Reading org numbers from files."""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import orgnr as orgnr_mod

_KEYS = ("organisasjonsnummer", "orgnr", "org_number", "orgNummer")
_NUMBER = re.compile(r"(?<!\d)(\d{3}\s?\d{3}\s?\d{3})(?!\d)")


def read_orgnrs(path: str) -> list[str]:
    """Text/CSV (one number per line, spaces allowed) or JSON/JSONL. De-duplicated, order kept."""
    text = Path(path).read_text(encoding="utf-8-sig")
    found: list[str] = []
    if text.lstrip()[:1] in ("[", "{"):
        try:
            data = json.loads(text)
            data = data if isinstance(data, list) else [data]
        except ValueError:
            data = [json.loads(line) for line in text.splitlines() if line.strip()]
        for item in data:
            if isinstance(item, dict):
                item = next((item[k] for k in _KEYS if k in item), "")
            found.append(str(item))
    else:
        for line in text.splitlines():
            m = _NUMBER.search(line)
            if m:
                found.append(m.group(1))
            elif line.strip() and not line.lower().startswith(("org", "#")):
                found.append(line.strip())  # kept so it is reported as invalid, not silently dropped
    return list(dict.fromkeys(orgnr_mod.normalize(x) or x for x in found))
