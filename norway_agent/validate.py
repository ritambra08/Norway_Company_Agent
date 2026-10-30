"""Self-check of an output folder: contract shape, availability states and evidence replay.

For every published claim the referenced evidence must exist, carry source/retrieval/hash, and its
span must be reproducible from the stored snapshot (JSON path value, or verbatim text span).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .claims import STATES, canon, jpath_get
from .inputs import read_batch
from .snapshots import read_snapshot

FIN_FIELDS = {"revenue", "operating_result", "profit_before_tax", "net_result", "total_assets", "equity",
              "total_liabilities", "personnel_costs"}


def validate(envelopes_path: Path, batch_path: str | None = None) -> tuple[int, list[str], dict]:
    envelopes_path = Path(envelopes_path)
    out_dir = envelopes_path.parent
    envs = [json.loads(l) for l in envelopes_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    problems: list[str] = []
    stats = {"envelopes": len(envs), "claims": 0, "available_claims": 0, "evidence_checked": 0, "spans_replayed": 0}
    seen = set()
    for env in envs:
        o = env.get("organisation_number")
        if o in seen:
            problems.append(f"{o}: duplicate envelope")
        seen.add(o)
        for k in ("run", "claims", "evidence", "changes", "errors", "operations", "status"):
            if k not in env:
                problems.append(f"{o}: missing '{k}'")
        if env.get("status") not in STATES:
            problems.append(f"{o}: invalid status {env.get('status')}")
        if (env.get("run") or {}).get("terminal_status") not in ("completed", "partial", "failed"):
            problems.append(f"{o}: invalid terminal_status")
        ev = {e["id"]: e for e in env.get("evidence") or []}
        cache: dict[str, bytes | None] = {}
        for c in env.get("claims") or []:
            stats["claims"] += 1
            if c.get("availability") not in STATES:
                problems.append(f"{o}/{c.get('field')}: invalid availability {c.get('availability')}")
                continue
            if c["availability"] != "available":
                if not c.get("reason"):
                    problems.append(f"{o}/{c['field']}: {c['availability']} without a reason")
                continue
            stats["available_claims"] += 1
            if c.get("value") is None:
                problems.append(f"{o}/{c['field']}: available claim with no value")
            if not c.get("evidence_ids"):
                problems.append(f"{o}/{c['field']}: available claim without evidence")
            if c["field"] in FIN_FIELDS:
                v = c.get("value") or {}
                if not v.get("currency") or not v.get("period_end"):
                    problems.append(f"{o}/{c['field']}: financial value without currency/period")
            for eid in c.get("evidence_ids") or []:
                e = ev.get(eid)
                if not e:
                    problems.append(f"{o}/{c['field']}: missing evidence {eid}")
                    continue
                for k in ("source_url", "retrieved_at", "content_sha256", "claim_span", "source_class"):
                    if not e.get(k) and e.get(k) != "":
                        problems.append(f"{o}/{eid}: evidence missing {k}")
        srcs = env.get("sources") or {}
        for eid, e in ev.items():
            src = srcs.get(e.get("source_ref") or "", {})
            if e.get("source_ref") and not src:
                problems.append(f"{o}/{eid}: evidence references unknown source {e.get('source_ref')}")
            snap = e.get("snapshot") or src.get("snapshot")
            if not snap:
                continue
            if snap not in cache:
                cache[snap] = read_snapshot(out_dir, snap)
            raw = cache[snap]
            if raw is None:
                continue  # snapshots may be pruned; hashes stay in the envelope
            stats["evidence_checked"] += 1
            if hashlib.sha256(raw).hexdigest() != e.get("content_sha256"):
                problems.append(f"{o}/{eid}: snapshot hash mismatch")
                continue
            loc = e.get("locator") or {}
            if loc.get("type") == "json_path":
                try:
                    data = json.loads(raw.decode("utf-8-sig"))
                except ValueError:
                    problems.append(f"{o}/{eid}: snapshot is not JSON")
                    continue
                if canon(jpath_get(data, loc.get("path", "")))[:2000] != e.get("claim_span"):
                    problems.append(f"{o}/{eid}: JSON span does not replay at {loc.get('path')}")
                else:
                    stats["spans_replayed"] += 1
                # identity guard: any registry record path must belong to this company
                if e.get("source_class") == "official_registry" and "enheter[" in loc.get("path", ""):
                    rec_path = loc["path"].split("].")[0] + "]"
                    rec = jpath_get(data, rec_path)
                    if isinstance(rec, dict) and rec.get("organisasjonsnummer") not in (o, None) and \
                            "underenheter" not in rec_path:
                        problems.append(f"{o}/{eid}: registry record belongs to {rec.get('organisasjonsnummer')}")
            elif loc.get("type") == "text_span":
                txt = None
                for enc in ("utf-8", "cp1252", "latin-1"):
                    try:
                        txt = raw.decode(enc)
                        break
                    except UnicodeDecodeError:
                        continue
                if txt is None or e.get("claim_span", "") not in txt:
                    problems.append(f"{o}/{eid}: text span not found in snapshot")
                else:
                    stats["spans_replayed"] += 1
    if batch_path:
        inputs, _ = read_batch(batch_path)
        missing = [x for x in inputs if x not in seen]
        extra = [x for x in seen if x not in set(inputs)]
        if missing:
            problems.append(f"{len(missing)} input(s) without an envelope, e.g. {missing[:3]}")
        if extra:
            problems.append(f"{len(extra)} envelope(s) not in the input, e.g. {extra[:3]}")
    return len(envs), problems, stats
