"""Decision-useful synthesis written only from the envelope's own available claims.

Deterministic templates: no model, no outside knowledge. Every sentence maps to claims, and the
claim fields used are listed so a reader can verify each statement.
"""

from __future__ import annotations

from datetime import datetime, timezone


def _v(claims: dict, f: str):
    c = claims.get(f)
    return c["value"] if c and c.get("availability") == "available" else None


def money(amount, currency: str = "NOK") -> str:
    if amount is None:
        return "?"
    a = float(amount)
    sign = "-" if a < 0 else ""
    a = abs(a)
    if a >= 1e9:
        s = f"{a / 1e9:.1f} billion"
    elif a >= 1e6:
        s = f"{a / 1e6:.1f} million"
    elif a >= 1e4:
        s = f"{a / 1e3:.0f} thousand"
    else:
        s = f"{a:,.0f}".replace(",", " ")
    return f"{sign}{currency} {s}"


def _city(addr) -> str | None:
    if not isinstance(addr, dict):
        return None
    c = addr.get("city") or addr.get("municipality")
    return c.title() if isinstance(c, str) else None


FORMS_EN = {"AS": "private limited company", "ASA": "public limited company", "ENK": "sole proprietorship",
            "NUF": "Norwegian branch of a foreign company", "DA": "partnership with shared liability",
            "ANS": "general partnership", "SA": "cooperative", "STI": "foundation", "BRL": "housing cooperative",
            "BBL": "housing building cooperative", "ESEK": "property co-ownership (section owners)",
            "FLI": "association", "SAM": "co-ownership", "KS": "limited partnership", "SPA": "savings bank",
            "IKS": "inter-municipal company", "SF": "state enterprise", "KF": "municipal enterprise",
            "PK": "pension fund", "GFS": "mutual insurance company", "BA": "company with limited liability",
            "SÆR": "entity under special legislation", "PRE": "shipping partnership", "VPFO": "securities fund"}


def build(env: dict) -> dict:
    claims = {c["field"]: c for c in env.get("claims") or []}
    used: set[str] = set()

    def v(f):
        x = _v(claims, f)
        if x is not None:
            used.add(f)
        return x

    name = v("legal_name") or env.get("organisation_number")
    brand = v("public_brand_name")
    fobj = v("organisation_form") or {}
    form = (f"{FORMS_EN[fobj['code']]} ({fobj['code']})" if fobj.get("code") in FORMS_EN
            else (fobj.get("description") or "").lower() or None)
    city = _city(v("business_address"))
    inds = v("industry") or []
    status = v("registry_status")
    what: list[str] = []
    head = f"{name}"
    if brand and brand.lower() not in str(name).lower():
        head += f" (trading as {brand})"
    head += f" is a{'n' if (form or 'x')[0].lower() in 'aeiou' else ''} {form or 'registered entity'}"
    if city:
        head += f" based in {city}"
    founded = v("founding_date") or v("registration_date")
    if founded:
        head += f", founded {founded[:4]}"
    what.append(head + ".")
    if inds:
        what.append(f"Registered industry: {(inds[0].get('description') or '').capitalize()} (NACE {inds[0].get('code')}).")
    desc = v("business_description") or {}
    act = desc.get("activity") or desc.get("statutory_purpose")
    if act:
        what.append(f"Registered activity: {act.rstrip('.')}.")
    site_desc = v("website_description")
    if site_desc:
        what.append(f"Its website describes it as: \"{site_desc[:200].rstrip('.')}\".")
    emp = v("registered_employees")
    if isinstance(emp, dict):
        what.append(f"Registered employees: {emp['count']}." if "count" in emp else
                    "Registered employees: fewer than 5 (the register withholds the exact number).")

    money_lines: list[str] = []
    latest = v("annual_accounts_latest")
    if latest:
        figs = latest.get("figures") or {}
        cur = latest.get("currency", "NOK")
        year = (latest.get("period_end") or "")[:4]
        parts = []
        if "revenue" in figs:
            parts.append(f"operating revenue of {money(figs['revenue'], cur)}")
        if "net_result" in figs:
            parts.append(f"a net result of {money(figs['net_result'], cur)}")
        if "equity" in figs:
            parts.append(f"equity of {money(figs['equity'], cur)}")
        grp = " (consolidated group accounts)" if latest.get("accounts_type") == "KONSERN" else ""
        if parts:
            money_lines.append(f"For {year}{grp} it reported " + ", ".join(parts) + ".")
        rc = v("revenue_change")
        if rc and rc.get("change_pct") is not None:
            word = "up" if rc["change"] > 0 else "down" if rc["change"] < 0 else "flat"
            money_lines.append(f"Revenue was {word} {abs(rc['change_pct'])}% from {rc['from_period_end'][:4]}.")
    flags: list[str] = []
    if status and status not in ("active",):
        flags.append(f"Register status: {status.replace('_', ' ')}.")
    if latest:
        eq = (latest.get("figures") or {}).get("equity")
        if isinstance(eq, (int, float)) and eq < 0:
            flags.append("Negative equity in the latest accounts.")
    lfy = v("latest_filed_accounts_year")
    if lfy and lfy.isdigit() and int(lfy) < datetime.now(timezone.utc).year - 1:
        flags.append(f"Latest annual accounts submitted are for {lfy}.")

    people: list[str] = []
    ceo = v("chief_executive")
    chair = v("board_chair")
    board = v("board_members") or []
    if isinstance(ceo, dict):
        people.append(f"{ceo.get('name')} is {('the owner' if ceo.get('role_code') == 'INNH' else 'chief executive')}")
    if isinstance(chair, dict):
        people.append(f"{chair.get('name')} chairs the board")
    if board:
        people.append(f"{len(board)} board member(s) in total")
    aud = v("auditor")
    if isinstance(aud, dict):
        people.append(f"auditor: {aud.get('name')}")

    where: list[str] = []
    wp = v("registered_workplaces")
    if wp and wp.get("items"):
        munis = sorted({(i.get("address") or {}).get("municipality", "").title() for i in wp["items"]} - {""})
        where.append(f"{wp.get('total', len(wp['items']))} registered workplace(s)"
                     + (f" in {', '.join(munis[:5])}" + (" and more" if len(munis) > 5 else "") if munis else ""))

    hiring: str
    jp = v("job_postings")
    hs = claims.get("hiring_signal") or {}
    if jp:
        newest = jp[0]
        hiring = (f"Hiring: {len(jp)} open posting(s), newest \"{newest.get('title')}\""
                  + (f" ({newest.get('published')})" if newest.get("published") else "") + ".")
    elif jp == [] and claims.get("job_postings", {}).get("availability") == "available":
        hiring = "Hiring: the company's careers page says there are no open positions."
    else:
        hiring = "Hiring: no open postings found in the job sources checked" + \
                 (" (" + "; ".join(f"{x['source'].split(' (')[0]}: {x['status']}" for x in hs.get("checked_sources") or []) + ")"
                  if hs.get("checked_sources") else "") + "."

    online: list[str] = []
    site = v("official_website")
    if site:
        online.append(f"Official website {site['url']} (verified: {site['verification'].replace('_', ' ')})")
    prof = v("social_profiles") or []
    if prof:
        disp = {"linkedin": "LinkedIn", "youtube": "YouTube", "tiktok": "TikTok", "x": "X"}
        online.append("profiles on " + ", ".join(sorted({disp.get(p['platform'], p['platform'].title()) for p in prof})))

    recent: list[str] = []
    for item in (v("public_activity") or [])[:4]:
        recent.append(f"{item['date']}: {item['title']}")

    changes = [c for c in env.get("changes") or [] if c.get("material")]
    refresh = env.get("refresh") or {}
    if refresh.get("mode") == "first_run":
        changed = "First profile for this company; no earlier run to compare with."
    elif changes:
        changed = f"{len(changes)} material change(s) since run {refresh.get('previous_run_id')}: " + "; ".join(
            sorted({c['type'].replace('_', ' ') + ' (' + c['field'].replace('_', ' ') + ')' for c in changes})[:6]) + "."
    else:
        changed = f"No material changes since run {refresh.get('previous_run_id')}."
    if refresh.get("carried_forward"):
        changed += (" Kept from the previous run because the source failed this time: "
                    + ", ".join(refresh["carried_forward"]) + ".")

    unknown = []
    for c in env.get("claims") or []:
        if c["availability"] != "available" and c["field"] in ("official_website", "job_postings", "annual_accounts_latest",
                                                                "leadership", "social_profiles", "website_news",
                                                                "registered_employees"):
            unknown.append({"field": c["field"], "state": c["availability"], "reason": c.get("reason")})

    text_parts = [" ".join(what)]
    if money_lines:
        text_parts.append(" ".join(money_lines))
    if people:
        text_parts.append("Leadership: " + "; ".join(people) + ".")
    if where:
        text_parts.append("Locations: " + "; ".join(where) + ".")
    text_parts.append(hiring)
    if online:
        text_parts.append("Online: " + "; ".join(online) + ".")
    if recent:
        text_parts.append("Recent activity: " + "; ".join(recent) + ".")
    if flags:
        text_parts.append("Watch: " + " ".join(flags))
    text_parts.append(changed)
    if unknown:
        text_parts.append("Unknown: " + "; ".join(f"{u['field'].replace('_', ' ')} ({u['state'].replace('_', ' ')})"
                                                  for u in unknown) + ".")
    return {
        "text": "\n".join(text_parts),
        "what_it_does": " ".join(what),
        "financial_snapshot": " ".join(money_lines) or None,
        "leadership": "; ".join(people) or None,
        "hiring": hiring,
        "online_presence": "; ".join(online) or None,
        "recent_activity": recent,
        "watch_flags": flags,
        "what_changed": changed,
        "unknowns": unknown,
        "based_on_fields": sorted(used),
        "method": "deterministic template over this envelope's available claims; no model",
    }
