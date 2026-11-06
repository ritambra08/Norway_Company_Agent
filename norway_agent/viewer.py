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


# --- Display text for the published site data (profiles.jsonl) ----------------------------------
# Plain English instead of register codes. Only display strings change: every key, list and number keeps
# its shape and value, and the envelopes (the scored output) are untouched.
ROLE_EN = {
    "Daglig leder": "Chief executive", "Daglig leder/ adm.direktør": "Chief executive",
    "Daglig leder/adm.direktør": "Chief executive", "Daglig leder / adm. direktør": "Chief executive",
    "Styrets leder": "Chair of the board", "Nestleder": "Deputy chair",
    "Styremedlem": "Board member", "Varamedlem": "Deputy board member", "Observatør": "Observer",
    "Revisor": "Auditor", "Regnskapsfører": "Accountant", "Forretningsfører": "Business manager",
    "Kontaktperson": "Contact person", "Innehaver": "Owner", "Komplementar": "General partner",
    "Deltaker med fullt ansvar": "Partner (full liability)", "Deltaker med delt ansvar": "Partner (shared liability)",
    "Bestyrende reder": "Managing shipowner", "Norsk representant for utenlandsk enhet": "Norwegian representative",
}
ACCOUNTS_TYPE_EN = {"SELSKAP": "Company accounts", "KONSERN": "Group accounts"}
STATUS_EN = {"active": "Active", "bankrupt": "Bankrupt", "under_liquidation": "In liquidation",
             "under_forced_dissolution": "Under forced dissolution", "closed": "Closed", "deleted": "Deleted"}
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_ISO_DATE = re.compile(r"(?<![\w/.-])(\d{4})-(\d{2})-(\d{2})(?![\w/-])")


def _nice_dates(text: str) -> str:
    """2021-05-09 -> 9 May 2021, except inside URLs or longer tokens."""
    def sub(m):
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return f"{d} {_MONTHS[mo - 1]} {y}" if 1 <= mo <= 12 and 1 <= d <= 31 else m.group(0)
    if not isinstance(text, str):
        return text
    return re.sub(r"\S+", lambda w: w.group(0) if "://" in w.group(0) else _ISO_DATE.sub(sub, w.group(0)), text)


def _once(phrases: list[str]) -> list[str]:
    """Each distinct phrase once, first letter capitalised, order kept."""
    out, seen = [], set()
    for p in phrases:
        p = (p or "").strip()
        if p and p.lower() not in seen:
            seen.add(p.lower())
            out.append(p[0].upper() + p[1:])
    return out


def _role_items(v):
    items = v if isinstance(v, list) else [v] if isinstance(v, dict) else []
    for it in items:
        if isinstance(it, dict) and it.get("role"):
            it["role"] = ROLE_EN.get(it["role"], it["role"])
            it.pop("group", None)  # the register's role group repeated the role ("Styre" + "Styremedlem")
    return v


def site_card(c: dict) -> dict:
    """A copy of a profile card with plain-English display text, for the published profiles.jsonl."""
    c = json.loads(json.dumps(c))
    facts = c.get("facts") or {}
    for f, x in facts.items():
        if x.get("text"):
            x["text"] = _nice_dates(x["text"])
        v = x.get("value")
        if v is None:
            continue
        if f in ("leadership", "board_members", "chief_executive", "board_chair", "auditor", "accountant"):
            _role_items(v)
        elif f == "annual_accounts_latest" and isinstance(v, dict):
            v["accounts_type"] = ACCOUNTS_TYPE_EN.get(v.get("accounts_type"), v.get("accounts_type"))
            for k in ("small_company_rules", "audit_opted_out"):
                if isinstance(v.get(k), bool):
                    v[k] = "Yes" if v[k] else "No"
        elif f == "annual_accounts_history" and isinstance(v, list):
            for it in v:
                if isinstance(it, dict) and it.get("accounts_type"):
                    it["accounts_type"] = ACCOUNTS_TYPE_EN.get(it["accounts_type"], it["accounts_type"])
        elif f == "registry_change_events" and isinstance(v, list):
            for it in v:
                if isinstance(it, dict):
                    if it.get("type") == "changed":
                        it["type"] = ""  # every entry is a change; the label only repeated that
                    elif isinstance(it.get("type"), str):
                        it["type"] = it["type"][:1].upper() + it["type"][1:]
                    if isinstance(it.get("changes"), list):
                        it["changes"] = _once(it["changes"])
        elif f == "role_change_events" and isinstance(v, list):
            for it in v:
                if isinstance(it, dict) and isinstance(it.get("type"), str):
                    it["type"] = it["type"][:1].upper() + it["type"][1:]
        elif f == "public_activity" and isinstance(v, list):
            for it in v:
                if isinstance(it, dict) and isinstance(it.get("title"), str) and "; " in it["title"]:
                    it["title"] = "; ".join(_once(it["title"].split("; ")))
        elif f == "institutional_sector" and isinstance(v, dict) and v.get("description"):
            x["text"] = f"{v['description']} ({v['code']})" if v.get("code") else v["description"]
            x.pop("value")
    rs = facts.get("registry_status")
    if rs and rs.get("text") in STATUS_EN:
        rs["text"] = STATUS_EN[rs["text"]]
    if isinstance(c.get("summary"), str):
        c["summary"] = _nice_dates(c["summary"])
    return c


def write_cards(envs: list[dict], path: Path) -> list[dict]:
    """Write the published site data (plain-English display text) and return the raw cards for the
    built-in viewer."""
    cards = [card(e) for e in envs]
    with Path(path).open("w", encoding="utf-8") as fh:
        for c in cards:
            fh.write(jsonl.dumps(site_card(c)) + "\n")
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
