"""Profile cards (profiles.jsonl) and a single-file HTML viewer (index.html).

The viewer works offline on desktop and mobile: search, filter, sort, compare up to three
companies, and open the evidence behind every fact (source link, retrieval time, hash, span).
"""

from __future__ import annotations

import base64
import html
import json
import re
from pathlib import Path

from . import jsonl
from .summary import money

KEY_FIELDS = ["legal_name", "industry", "business_address", "annual_accounts_latest", "leadership",
              "registered_workplaces", "official_website", "social_profiles", "job_postings", "public_activity",
              "registered_employees", "business_description"]


def _strip(s: str) -> str:
    s = re.sub(r"<script.*?</script>|<style.*?</style>", " ", s or "", flags=re.S | re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", html.unescape(s)).strip()[:400]


def _quote(span: str) -> str:
    """Evidence span for display: JSON-encoded values are shown as the value itself."""
    try:
        v = json.loads(span)
    except (ValueError, TypeError):
        return _strip(span)
    if isinstance(v, str):
        return v
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return f"{v:,.2f}".replace(",", " ").rstrip("0").rstrip(".") if isinstance(v, float) else str(v)
    return _strip(span)


def _fmt(field: str, v) -> str:
    if v is None:
        return ""
    if isinstance(v, dict) and "amount" in v:
        return money(v["amount"], v.get("currency", "NOK")) + (f" ({v['period_end'][:4]})" if v.get("period_end") else "")
    if field in ("business_address", "postal_address") and isinstance(v, dict):
        return ", ".join(x for x in [" ".join(v.get("lines") or []), v.get("postal_code", ""), v.get("city", "")] if x)
    if field == "industry" and isinstance(v, list):
        return "; ".join(f"{i.get('description')} ({i.get('code')})" for i in v)
    if field == "organisation_form" and isinstance(v, dict):
        return f"{v.get('description')} ({v.get('code')})"
    if field == "registered_employees" and isinstance(v, dict):
        return str(v.get("count", "fewer than 5"))
    if field == "business_description" and isinstance(v, dict):
        return v.get("activity") or v.get("statutory_purpose") or ""
    if field == "official_website" and isinstance(v, dict):
        return v.get("url", "")
    if field in ("vat_registered", "business_register") and isinstance(v, dict):
        return ("Yes" if v.get("registered") else "No") + (f", since {v['since']}" if v.get("since") else "")
    if field == "share_capital" and isinstance(v, dict):
        return money(v.get("amount"), v.get("currency", "NOK"))
    if field == "revenue_change" and isinstance(v, dict):
        return (f"{v['change_pct']:+.1f}% ({v['from_period_end'][:4]}→{v['to_period_end'][:4]})"
                if v.get("change_pct") is not None else "")
    if field == "registered_contact" and isinstance(v, dict):
        return " · ".join(str(x) for x in v.values())
    if field in ("chief_executive", "board_chair", "auditor", "accountant"):
        items = v if isinstance(v, list) else [v]
        return "; ".join(str(i.get("name")) for i in items if isinstance(i, dict))
    if field == "hiring_signal" and isinstance(v, dict):
        return (f"Yes – {v.get('open_postings')} open posting(s)" if v.get("hiring")
                else "No – the careers page says there are no open positions")
    if isinstance(v, bool):
        return "Yes" if v else "No"
    if isinstance(v, (str, int, float)):
        return str(v)
    return ""


def card(env: dict) -> dict:
    claims = {c["field"]: c for c in env.get("claims") or []}
    ev = {e["id"]: e for e in env.get("evidence") or []}
    srcs = env.get("sources") or {}

    table: list[dict] = []
    index: dict[str, int] = {}

    def evs(c):
        out = []
        for i in c.get("evidence_ids") or []:
            e = ev.get(i)
            if not e:
                continue
            if i not in index:
                src = srcs.get(e.get("source_ref") or "", {})
                index[i] = len(table)
                table.append({"s": src.get("source_name") or e.get("source_class"), "u": e.get("source_url"),
                              "t": e.get("retrieved_at"), "h": (e.get("content_sha256") or "")[:12],
                              "x": _quote(e.get("claim_span", ""))[:160],
                              "l": (e.get("locator") or {}).get("path") or e.get("extraction_method"),
                              "p": 1 if e.get("from_previous_run") else 0})
            out.append(index[i])
            if len(out) >= 4:
                break
        return out

    facts = {}
    for f, c in claims.items():
        text = _fmt(f, c.get("value")) if c["availability"] == "available" else ""
        x = {"label": c["label"], "state": c["availability"], "text": text}
        if c["availability"] == "available":
            if not text:
                x["value"] = c.get("value")
            x["ev"] = evs(c)
            if c.get("note"):
                x["note"] = c["note"]
            if c.get("stale"):
                x["stale"] = True
        else:
            x["reason"] = c.get("reason")
        facts[f] = x
    def val(f):
        c = claims.get(f)
        return c["value"] if c and c["availability"] == "available" else None
    latest = val("annual_accounts_latest") or {}
    figs = latest.get("figures") or {}
    addr = val("business_address") or {}
    emp = val("registered_employees") or {}
    jobs = val("job_postings")
    present = sum(1 for f in KEY_FIELDS if val(f) not in (None, [], {}))
    return {
        "org": env["organisation_number"], "status": env["status"],
        "name": val("legal_name") or (env.get("legal_identity") or {}).get("name") or env["organisation_number"],
        "brand": val("public_brand_name"), "form": (val("organisation_form") or {}).get("code"),
        "city": (addr.get("city") or addr.get("municipality") or "").title(),
        "industry": ((val("industry") or [{}])[0]).get("description"),
        "registry_status": val("registry_status"),
        "revenue": figs.get("revenue"), "net_result": figs.get("net_result"), "currency": latest.get("currency"),
        "year": (latest.get("period_end") or "")[:4] or None, "employees": emp.get("count"),
        "website": (val("official_website") or {}).get("url"),
        "jobs": len(jobs) if isinstance(jobs, list) else None,
        "completeness": round(present / len(KEY_FIELDS), 2),
        "changes": env.get("changes") or [], "summary": (env.get("summary") or {}).get("text", ""),
        "sections": env.get("availability") or {}, "facts": facts,
        "run": (env.get("run") or {}).get("run_id"), "errors": env.get("errors") or [], "E": table,
    }


def write_cards(envs: list[dict], path: Path) -> list[dict]:
    cards = [card(e) for e in envs]
    with Path(path).open("w", encoding="utf-8") as fh:
        for c in cards:
            fh.write(jsonl.dumps(c) + "\n")
    return cards


ASSETS = Path(__file__).resolve().parent / "assets"


def _font(weight: int) -> str:
    f = ASSETS / f"public-sans-{weight}.woff2"
    return base64.b64encode(f.read_bytes()).decode("ascii") if f.exists() else ""


def write_viewer(cards: list[dict], report: dict, path: Path) -> None:
    """Single-file offline site: find, compare two, and open the source behind every fact.
    Public Sans (SIL OFL 1.1, see assets/PUBLIC_SANS_OFL.txt) is embedded so it renders the same offline."""
    data = jsonl.dumps({"cards": cards, "report": {k: report.get(k) for k in
                                                  ("run_id", "completed_at", "status_counts", "coverage_by_family",
                                                   "envelopes_written", "requests")}},
                       separators=(",", ":")).replace("</", "<\\/")
    html_text = (ASSETS / "viewer.html").read_text(encoding="utf-8")
    for w in (400, 600, 800):
        html_text = html_text.replace(f"__F{w}__", _font(w))
    Path(path).write_text(html_text.replace("__DATA__", data), encoding="utf-8")
