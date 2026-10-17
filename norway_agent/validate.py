"""Self-check for submitted profiles.

Checks the evidence rules and, when raw snapshots are present, *replays* the
extraction and confirms every stored value is exactly what the source said.
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

from .extract import extract
from .models import SCHEMA_VERSION
from .store import Store, canon

PUBLISHED = {"ok", "partial", "deleted"}


def validate_profile(p: dict, store: Store | None) -> list[str]:
    problems: list[str] = []
    org = p.get("organisasjonsnummer", "?")
    if p.get("schema_version") != SCHEMA_VERSION:
        problems.append(f"{org}: unexpected schema_version {p.get('schema_version')!r}")
    if p.get("status") not in PUBLISHED:
        return problems  # nothing published, nothing to verify

    for f in p.get("facts", []):
        tag = f"{org}/{f.get('key')}"
        s = f.get("source") or {}
        if f.get("subject_orgnr") != org:
            problems.append(f"{tag}: fact belongs to {f.get('subject_orgnr')}, not {org} (wrong-company)")
        if s.get("verified_match") is not True:
            problems.append(f"{tag}: source record was not identity-verified")
        if urlparse(s.get("url", "")).hostname != "data.brreg.no":
            problems.append(f"{tag}: source url is not a data.brreg.no API url")
        if not s.get("retrieved_at"):
            problems.append(f"{tag}: missing retrieved_at")
        if not s.get("record_url"):
            problems.append(f"{tag}: missing record_url")
        v = f.get("value")
        if isinstance(v, dict) and "amount" in v and not (v.get("currency") and v.get("period_end")):
            problems.append(f"{tag}: financial value without currency/period")

    if store is not None and p.get("status") in PUBLISHED and not p.get("stale"):
        snaps = store.load_raw(org)
        if snaps:
            replay = {f.key: f.value for f in extract(org, snaps).facts}
            stored = {f["key"]: f["value"] for f in p.get("facts", [])}
            for k, v in stored.items():
                if k not in replay:
                    problems.append(f"{org}/{k}: stored fact cannot be reproduced from the raw snapshot")
                elif canon(replay[k]) != canon(v):
                    problems.append(f"{org}/{k}: stored value differs from the raw snapshot")
    return problems


def validate_dir(out: Path) -> tuple[int, list[str]]:
    store = Store(out)
    jl = Path(out) / "profiles.jsonl"
    profiles = [json.loads(line) for line in jl.read_text(encoding="utf-8").splitlines() if line.strip()]
    problems: list[str] = []
    for p in profiles:
        problems.extend(validate_profile(p, store))
    return len(profiles), problems
