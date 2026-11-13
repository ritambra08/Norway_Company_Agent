"""Decision-useful synthesis written only from the envelope's own available claims.

Deterministic templates: no model, no outside knowledge. Every sentence is a *statement* that names
the claims it rests on, their evidence ids and numbered sources; the summary text carries the source
numbers inline ("... founded 2003 [1].") and ``sources`` lists what each number points to.

It answers the three questions a reader has: what the company does, what changed (dated register
changes in the last 12 months, plus differences from the previous run) and what remains unknown.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


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

# English names of the NACE divisions (first two digits), worded to hold for both NACE Rev. 2 (SN2007) and
# Rev. 2.1 (SN2025), so a reader who does not read Norwegian knows the line of business.
NACE_DIVISION_EN = {
    "01": "crop and animal production, hunting and related services", "02": "forestry and logging",
    "03": "fishing and aquaculture", "05": "mining of coal and lignite",
    "06": "extraction of crude petroleum and natural gas", "07": "mining of metal ores",
    "08": "other mining and quarrying", "09": "mining support services", "10": "manufacture of food products",
    "11": "manufacture of beverages", "12": "manufacture of tobacco products", "13": "manufacture of textiles",
    "14": "manufacture of clothing", "15": "manufacture of leather and related products",
    "16": "manufacture of wood and wood products", "17": "manufacture of paper and paper products",
    "18": "printing and reproduction of recorded media", "19": "manufacture of coke and refined petroleum products",
    "20": "manufacture of chemicals", "21": "manufacture of pharmaceutical products",
    "22": "manufacture of rubber and plastic products", "23": "manufacture of other non-metallic mineral products",
    "24": "manufacture of basic metals", "25": "manufacture of fabricated metal products",
    "26": "manufacture of computer, electronic and optical products", "27": "manufacture of electrical equipment",
    "28": "manufacture of machinery and equipment", "29": "manufacture of motor vehicles and trailers",
    "30": "manufacture of other transport equipment", "31": "manufacture of furniture", "32": "other manufacturing",
    "33": "repair and installation of machinery and equipment",
    "35": "electricity, gas, steam and air conditioning supply", "36": "water collection, treatment and supply",
    "37": "sewerage", "38": "waste collection, treatment and materials recovery",
    "39": "remediation and other waste management services",
    "41": "construction of buildings", "42": "civil engineering", "43": "specialised construction work",
    "45": "trade and repair of motor vehicles", "46": "wholesale trade", "47": "retail trade",
    "49": "land transport", "50": "water transport", "51": "air transport",
    "52": "warehousing and support activities for transport", "53": "postal and courier activities",
    "55": "accommodation", "56": "food and beverage service activities", "58": "publishing",
    "59": "film, video, TV and sound recording production", "60": "programming, broadcasting and content distribution",
    "61": "telecommunications", "62": "computer programming, consultancy and related activities",
    "63": "information services, data processing and hosting", "64": "financial services (including holding companies)",
    "65": "insurance and pension funding", "66": "activities auxiliary to financial services and insurance",
    "68": "real estate activities", "69": "legal and accounting activities",
    "70": "head office activities and management consultancy", "71": "architectural and engineering activities",
    "72": "scientific research and development", "73": "advertising, market research and public relations",
    "74": "other professional, scientific and technical activities", "75": "veterinary activities",
    "77": "rental and leasing", "78": "employment activities", "79": "travel agency and tour operator activities",
    "80": "security and investigation activities", "81": "services to buildings and landscaping",
    "82": "office administration and other business support", "84": "public administration and defence",
    "85": "education", "86": "human health activities", "87": "residential care activities",
    "88": "social work without accommodation", "90": "arts and performing arts activities",
    "91": "libraries, archives, museums and other cultural activities", "92": "gambling and betting",
    "93": "sports, amusement and recreation activities", "94": "activities of membership organisations",
    "95": "repair of computers, household goods and vehicles", "96": "personal service activities",
    "97": "households as employers of domestic staff", "99": "extraterritorial organisations",
}

SOURCE_NAMES = {"official_registry": "Brønnøysund Register Centre – Central Coordinating Register (Enhetsregisteret)",
                "official_accounts": "Brønnøysund Register Centre – Register of Company Accounts",
                "public_job_feed": "NAV job vacancy feed (arbeidsplassen.nav.no)",
                "company_owned": "the company's verified website",
                "company_linked": "recruitment page linked from the company's website"}

UNKNOWN_TEXT = {
    "official_website": "official website",
    "job_postings": "open job postings",
    "annual_accounts_latest": "annual accounts",
    "leadership": "current roles",
    "social_profiles": "company-owned social profiles",
    "website_news": "news on the company's own website",
    "registered_employees": "employee count",
}
# Why each is unknown, in one plain phrase per availability state (the claim's reason has the detail).
UNKNOWN_WHY = {
    ("official_website", "not_available"): "no website could be tied to this company by its organisation number, "
                                           "registered contact details or registered address",
    ("official_website", "ambiguous"): "the declared website does not show this company (it names another entity "
                                       "or not this one), so it is not attributed",
    ("official_website", "blocked"): "the website does not allow automated reading (robots.txt)",
    ("official_website", "failed"): "the website could not be read in this run",
    ("job_postings", "not_available"): "none in the NAV job feed or on a company careers page; Finn.no and "
                                       "JavaScript-only career sites are not covered",
    ("annual_accounts_latest", "not_available"): "no filed accounts in the open accounts register",
    ("annual_accounts_latest", "not_applicable"): "this kind of entity does not file annual accounts",
    ("leadership", "not_available"): "no roles are registered",
    ("social_profiles", "not_available"): "none linked from a verified company website",
    ("social_profiles", "ambiguous"): "the website is shared with a group or brand, so its profiles are not attributed",
    ("website_news", "not_available"): "no dated news on a verified company website",
    ("website_news", "ambiguous"): "the website is shared with a group or brand, so its news is not attributed",
    ("registered_employees", "not_available"): "no employee count is registered (not the same as zero)",
}


def _why(field: str, state: str) -> str:
    return UNKNOWN_WHY.get((field, state)) or {"failed": "the source could not be read in this run",
                                               "blocked": "the source does not allow automated reading",
                                               "ambiguous": "the evidence was conflicting, so nothing is published",
                                               "not_applicable": "does not apply to this entity"}.get(
        state, "not found in the sources checked")


def build(env: dict, now: datetime | None = None) -> dict:
    claims = {c["field"]: c for c in env.get("claims") or []}
    evidence = {e["id"]: e for e in env.get("evidence") or []}
    now = now or datetime.now(timezone.utc)
    year_ago = (now - timedelta(days=365)).date().isoformat()
    used: set[str] = set()
    statements: list[dict] = []
    src_index: dict[str, int] = {}
    src_list: list[dict] = []

    def v(f):
        x = _v(claims, f)
        if x is not None:
            used.add(f)
        return x

    def refs(fields, evidence_ids=()) -> tuple[list[str], list[int]]:
        ids: list[str] = []
        for f in fields:
            c = claims.get(f)
            if c:
                ids += c.get("evidence_ids") or []
        ids += list(evidence_ids)
        ids = list(dict.fromkeys(i for i in ids if i in evidence))
        nums: list[int] = []
        for i in ids:
            e = evidence[i]
            key = e.get("source_url") or ""
            if not key:
                continue
            if key not in src_index:
                src_index[key] = len(src_list) + 1
                src_list.append({"n": src_index[key], "name": SOURCE_NAMES.get(e.get("source_class"),
                                                                                e.get("source_class")),
                                 "url": key, "retrieved_at": e.get("retrieved_at")})
            if src_index[key] not in nums:
                nums.append(src_index[key])
        return ids, sorted(nums)

    def say(section: str, text: str, fields=(), evidence_ids=()) -> dict:
        ids, nums = refs(fields, evidence_ids)
        st = {"section": section, "text": text, "fields": [f for f in fields if f in claims],
              "evidence_ids": ids, "sources": nums}
        statements.append(st)
        return st

    # ---- what it does -------------------------------------------------------------------------
    name = v("legal_name") or env.get("organisation_number")
    brand = v("public_brand_name")
    fobj = v("organisation_form") or {}
    form = (f"{FORMS_EN[fobj['code']]} ({fobj['code']})" if fobj.get("code") in FORMS_EN
            else (fobj.get("description") or "").lower() or None)
    city = _city(v("business_address"))
    inds = v("industry") or []
    status = v("registry_status")
    head = f"{name}"
    show_brand = bool(brand and brand.lower() not in str(name).lower())
    if show_brand:
        head += f" (trading as {brand})"
    head += f" is a{'n' if (form or 'x')[0].lower() in 'aeiou' else ''} {form or 'registered entity'}"
    if city:
        head += f" based in {city}"
    founded = v("founding_date") or v("registration_date")
    if founded:
        head += f", founded {founded[:4]}"
    say("what_it_does", head + ".", ["legal_name", "organisation_form", "business_address", "founding_date",
                                     "registration_date"] + (["public_brand_name"] if show_brand else []))
    if inds:
        code = str(inds[0].get("code") or "")
        div = NACE_DIVISION_EN.get(code[:2])
        say("what_it_does", f"Registered industry: {(inds[0].get('description') or '').capitalize()} "
                            f"(NACE {code}" + (f"; line of business: {div}" if div else "") + ").", ["industry"])
    desc = v("business_description") or {}
    act = desc.get("activity") or desc.get("statutory_purpose")
    if act:
        say("what_it_does", f"Registered activity (as written in the register): {act.rstrip('.')}.",
            ["business_description"])
    site_desc = v("website_description")
    if site_desc:
        say("what_it_does", f"Its website describes it as: \"{site_desc[:200].rstrip('.')}\".",
            ["website_description"])
    emp = v("registered_employees")
    if isinstance(emp, dict):
        say("what_it_does", f"Registered employees: {emp['count']}." if "count" in emp else
            "Registered employees: fewer than 5 (the register withholds the exact number).", ["registered_employees"])

    # ---- money ------------------------------------------------------------------------------------
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
            say("financial_snapshot", f"For {year}{grp} it reported " + ", ".join(parts) + ".",
                ["annual_accounts_latest"])
        rc = v("revenue_change")
        if rc and rc.get("change_pct") is not None:
            word = "up" if rc["change"] > 0 else "down" if rc["change"] < 0 else "flat"
            say("financial_snapshot", f"Revenue was {word} {abs(rc['change_pct'])}% from {rc['from_period_end'][:4]}.",
                ["revenue_change"])
    if status and status not in ("active",):
        say("watch_flags", f"Register status: {status.replace('_', ' ')}.", ["registry_status"])
    if latest:
        eq = (latest.get("figures") or {}).get("equity")
        if isinstance(eq, (int, float)) and eq < 0:
            say("watch_flags", "Negative equity in the latest accounts.", ["annual_accounts_latest"])
    lfy = v("latest_filed_accounts_year")
    if lfy and lfy.isdigit() and int(lfy) < now.year - 1:
        say("watch_flags", f"Latest annual accounts submitted are for {lfy}.", ["latest_filed_accounts_year"])

    # ---- people and places ---------------------------------------------------------------------
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
    if people:
        say("leadership", "Leadership: " + "; ".join(people) + ".",
            ["chief_executive", "board_chair", "board_members", "auditor"])
    wp = v("registered_workplaces")
    where = ""
    if wp and wp.get("items"):
        munis = sorted({(i.get("address") or {}).get("municipality", "").title() for i in wp["items"]} - {""})
        where = (f"{wp.get('total', len(wp['items']))} registered workplace(s)"
                 + (f" in {', '.join(munis[:5])}" + (" and more" if len(munis) > 5 else "") if munis else ""))
        say("locations", f"Locations: {where}.", ["registered_workplaces"])

    # ---- hiring -------------------------------------------------------------------------------------
    jp = v("job_postings")
    hs = claims.get("hiring_signal") or {}
    if jp:
        newest = jp[0]
        hiring = (f"Hiring: {len(jp)} open posting(s), newest \"{newest.get('title')}\""
                  + (f" ({newest.get('published')})" if newest.get("published") else "") + ".")
        say("hiring", hiring, ["job_postings"])
    elif jp == [] and claims.get("job_postings", {}).get("availability") == "available":
        hiring = "Hiring: the company's careers page says there are no open positions."
        say("hiring", hiring, ["job_postings"])
    else:
        hiring = "Hiring: no open postings found in the job sources checked" + \
                 (" (" + "; ".join(f"{x['source'].split(' (')[0]}: {x['status']}" for x in hs.get("checked_sources") or []) + ")"
                  if hs.get("checked_sources") else "") + "."
        say("hiring", hiring)

    # ---- online presence ---------------------------------------------------------------------------
    online: list[str] = []
    site = v("official_website")
    if site:
        online.append(f"Official website {site['url']} (verified: {site['verification'].replace('_', ' ')})")
    prof = v("social_profiles") or []
    if prof:
        disp = {"linkedin": "LinkedIn", "youtube": "YouTube", "tiktok": "TikTok", "x": "X"}
        online.append("profiles on " + ", ".join(sorted({disp.get(p['platform'], p['platform'].title()) for p in prof})))
    if online:
        say("online_presence", "Online: " + "; ".join(online) + ".", ["official_website", "social_profiles"])

    recent: list[str] = []
    two_years = (now - timedelta(days=730)).date().isoformat()
    for item in [i for i in (v("public_activity") or []) if (i.get("date") or "") >= two_years][:4]:
        recent.append(f"{item['date']}: {item['title']}")
    if recent:
        say("recent_activity", "Recent activity: " + "; ".join(recent) + ".", ["public_activity"])

    # ---- what changed -----------------------------------------------------------------------------
    changed_parts: list[str] = []
    for h in v("former_names") or []:
        to = (h.get("to") or "")[:10]
        if to and to >= year_ago and h.get("name"):
            st = say("what_changed", f"On {to} it changed its registered name from {h['name']} to {name}.",
                     ["former_names", "legal_name"])
            changed_parts.append(st["text"])
    reg_events = [e for e in (v("registry_change_events") or []) if (e.get("date") or "") >= year_ago]
    if reg_events:
        bits = [f"{e['date']}: {'; '.join(e.get('changes') or [e.get('type', 'changed')])}" for e in reg_events[:3]]
        st = say("what_changed", "In the last 12 months the register recorded: " + "; ".join(bits) + ".",
                 ["registry_change_events"])
        changed_parts.append(st["text"])
    role_ev = [e for e in (v("role_change_events") or []) if (e.get("date") or "") >= year_ago]
    if role_ev:
        st = say("what_changed", f"Its registered roles (board, chief executive or auditor) were updated on "
                                 f"{role_ev[0]['date']}" + (f" and {len(role_ev) - 1} earlier date(s) this year"
                                                             if len(role_ev) > 1 else "") + ".",
                 ["role_change_events", "leadership"])
        changed_parts.append(st["text"])
    rce = claims.get("registry_change_events") or {}
    if not changed_parts and rce.get("availability") == "not_available" and \
            str(rce.get("reason", "")).startswith("No field-level changes"):
        st = say("what_changed", "The register's change feed shows no changes to it in the last 12 months.")
        changed_parts.append(st["text"])

    changes = [c for c in env.get("changes") or [] if c.get("material")]
    refresh = env.get("refresh") or {}
    if refresh.get("mode") == "first_run":
        run_line = "First profile for this company; no earlier run to compare with."
    elif changes:
        run_line = f"{len(changes)} material change(s) since run {refresh.get('previous_run_id')}: " + "; ".join(
            sorted({c['type'].replace('_', ' ') + ' (' + c['field'].replace('_', ' ') + ')' for c in changes})[:6]) + "."
    else:
        run_line = f"No material changes since run {refresh.get('previous_run_id')}."
    if refresh.get("carried_forward"):
        run_line += (" Kept from the previous run because the source failed this time: "
                     + ", ".join(refresh["carried_forward"]) + ".")
    change_ids = [i for c in changes for i in (c.get("evidence_ids") or [])]
    say("what_changed", run_line, evidence_ids=change_ids)
    changed = " ".join(changed_parts + [run_line])

    # ---- what remains unknown -----------------------------------------------------------------
    unknown = []
    for c in env.get("claims") or []:
        if c["availability"] != "available" and c["field"] in UNKNOWN_TEXT:
            unknown.append({"field": c["field"], "state": c["availability"], "reason": c.get("reason")})
    if unknown:
        say("unknowns", "Not found or not confirmed: " + "; ".join(
            f"{UNKNOWN_TEXT[u['field']]} ({_why(u['field'], u['state'])})" for u in unknown) + ".")

    # ---- assemble ----------------------------------------------------------------------------------
    def cite(st: dict) -> str:
        t = st["text"]
        if not st["sources"]:
            return t
        mark = " [" + ", ".join(str(n) for n in st["sources"]) + "]"
        return (t[:-1] + mark + ".") if t.endswith(".") else t + mark

    order = ["what_it_does", "financial_snapshot", "leadership", "locations", "hiring", "online_presence",
             "recent_activity", "watch_flags", "what_changed", "unknowns"]
    lines, plain = [], []
    for sec in order:
        group = [st for st in statements if st["section"] == sec]
        if group:
            lines.append(" ".join(cite(st) for st in group))
            plain.append(" ".join(st["text"] for st in group))

    def sec_text(sec: str) -> str | None:
        return " ".join(st["text"] for st in statements if st["section"] == sec) or None

    return {
        "text": "\n".join(lines),
        "plain_text": "\n".join(plain),
        "what_it_does": sec_text("what_it_does"),
        "financial_snapshot": sec_text("financial_snapshot"),
        "leadership": "; ".join(people) or None,
        "hiring": hiring,
        "online_presence": "; ".join(online) or None,
        "recent_activity": recent,
        "watch_flags": [st["text"] for st in statements if st["section"] == "watch_flags"],
        "what_changed": changed,
        "unknowns": unknown,
        "statements": statements,
        "sources": src_list,
        "based_on_fields": sorted(used),
        "method": "deterministic template over this envelope's available claims; no model. Each statement "
                  "lists the claims and evidence it rests on; [n] in the text points to sources[n].",
    }
