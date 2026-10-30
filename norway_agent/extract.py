"""Deterministic extraction: collected responses -> claims with evidence and availability states.

Safety rules:
  * a claim is built only from a record whose own organisation number is the requested one;
  * numbers are copied verbatim with currency and period; derived values say how they were derived;
  * absence is stated with a reason and the sources checked, never written as zero;
  * birth dates and job-ad contact persons are never copied.
"""

from __future__ import annotations

import html
import re
from urllib.parse import urlsplit

from .claims import Builder, canon, jpath_get
from .state import CompanyState, SourceStatus

FIN = [
    ("revenue", "resultatregnskapResultat.driftsresultat.driftsinntekter.sumDriftsinntekter"),
    ("operating_result", "resultatregnskapResultat.driftsresultat.driftsresultat"),
    ("profit_before_tax", "resultatregnskapResultat.ordinaertResultatFoerSkattekostnad"),
    ("net_result", "resultatregnskapResultat.aarsresultat"),
    ("total_assets", "eiendeler.sumEiendeler"),
    ("equity", "egenkapitalGjeld.egenkapital.sumEgenkapital"),
    ("total_liabilities", "egenkapitalGjeld.gjeldOversikt.sumGjeld"),
    ("personnel_costs", "resultatregnskapResultat.driftsresultat.driftskostnad.loennskostnad"),
]
TYPE_RANK = {"SELSKAP": 0, "KONSERN": 1}

CHANGE_LABELS = {
    "navn": "name changed", "forretningsadresse": "business address changed", "postadresse": "postal address changed",
    "naeringskode1": "primary industry code changed", "naeringskode2": "secondary industry code changed",
    "antallAnsatte": "registered employee count updated", "hjemmeside": "website changed",
    "epostadresse": "email changed", "telefon": "phone changed", "mobil": "mobile changed",
    "sisteInnsendteAarsregnskap": "annual accounts registered", "konkurs": "bankruptcy status changed",
    "underAvvikling": "liquidation status changed", "underTvangsavviklingEllerTvangsopplosning":
    "forced dissolution status changed", "vedtektsfestetFormaal": "statutory purpose changed",
    "aktivitet": "registered activity changed", "organisasjonsform": "legal form changed",
    "kapital": "share capital changed", "vedtektsdato": "articles of association updated",
    "registrertIMvaregisteret": "VAT registration changed", "institusjonellSektorkode": "sector code changed",
    "erIKonsern": "group membership changed", "registrertIForetaksregisteret": "business register status changed",
}
NOISE_PATHS = {"registreringsdatoAntallAnsatteEnhetsregisteret", "registreringsdatoAntallAnsatteNAVAaregisteret"}


def _has(v) -> bool:
    return v is not None and v != "" and v != [] and v != {}


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _p(base: str, key: str) -> str:
    return f"{base}.{key}" if base else key


def _addr(a) -> dict | None:
    if not isinstance(a, dict):
        return None
    out = {"lines": [x for x in (a.get("adresse") or []) if x], "postal_code": a.get("postnummer"),
           "city": a.get("poststed"), "municipality": a.get("kommune"),
           "municipality_number": a.get("kommunenummer"), "country": a.get("land")}
    out = {k: v for k, v in out.items() if _has(v)}
    return out or None


def _status_reason(st: SourceStatus | None, what: str) -> tuple[str, str]:
    st = st or SourceStatus("not_checked")
    if st.state == "not_applicable":
        return "not_applicable", st.detail
    if st.state == "blocked":
        return "blocked", f"{what}: {st.detail}"
    if st.state in ("failed",):
        return "failed", f"{what} could not be read ({st.detail})"
    if st.state == "not_checked":
        return "failed", f"{what} was not checked in this run ({st.detail or 'budget'})"
    return "not_available", f"{what}: {st.detail}"


def _raw_span(resp, needle: str, pad: int = 50) -> str | None:
    if not needle:
        return None
    raw = resp.text()
    for cand in (needle, html.escape(needle, quote=False), html.escape(needle),
                 needle.replace("/", "\\/")):
        i = raw.find(cand)
        if i < 0:
            i = raw.lower().find(cand.lower())
        if i >= 0:
            return raw[max(0, i - pad):i + len(cand) + pad]
    return None


# ---------------------------------------------------------------------------------------------

BRREG = "https://data.brreg.no/enhetsregisteret/api"


def record_url(s: CompanyState) -> str:
    return f"{BRREG}/{'underenheter' if s.kind == 'underenhet' else 'enheter'}/{s.orgnr}"


def _identity(b: Builder, s: CompanyState) -> None:
    rec = s.record
    r, base, body = rec.resp, rec.path, rec.data or {}
    canon_url = record_url(s)

    def ev(*keys):
        return [b.ev_json(r, _p(base, k), source_url=canon_url) for k in keys if k in body]

    if _has(body.get("navn")):
        b.put("legal_name", body["navn"], ev("navn"))
    form = body.get("organisasjonsform") or {}
    if _has(form.get("kode")):
        b.put("organisation_form", {"code": form.get("kode"), "description": form.get("beskrivelse")},
              ev("organisasjonsform"))
    if s.identity.state == "deleted":
        b.put("registry_status", "deleted", ev(*[k for k in ("slettedato", "organisasjonsnummer") if k in body]),
              note=f"Deleted from the register{(' on ' + body['slettedato']) if body.get('slettedato') else ''}.")
    else:
        flags = [k for k in ("konkurs", "underAvvikling", "underTvangsavviklingEllerTvangsopplosning") if k in body]
        if flags:
            st = ("bankrupt" if body.get("konkurs") else "under_forced_dissolution"
                  if body.get("underTvangsavviklingEllerTvangsopplosning") else "under_liquidation"
                  if body.get("underAvvikling") else "active")
            b.put("registry_status", st, ev(*flags),
                  note="Derived only from the register's bankruptcy/liquidation/forced-dissolution flags.")
        elif s.kind == "underenhet":
            st = "closed" if body.get("nedleggelsesdato") else "active"
            b.put("registry_status", st, ev("nedleggelsesdato") or ev("organisasjonsnummer"))
    for field, key in (("registration_date", "registreringsdatoEnhetsregisteret"), ("founding_date", "stiftelsesdato")):
        if _has(body.get(key)):
            b.put(field, body[key], ev(key))
    inds = []
    for rank, key in enumerate(("naeringskode1", "naeringskode2", "naeringskode3"), 1):
        v = body.get(key)
        if isinstance(v, dict) and v.get("kode"):
            inds.append({"code": v["kode"], "description": v.get("beskrivelse"), "rank": rank})
    if inds:
        b.put("industry", inds, ev("naeringskode1", "naeringskode2", "naeringskode3"))
    sec = body.get("institusjonellSektorkode")
    if isinstance(sec, dict) and sec.get("kode"):
        b.put("institutional_sector", {"code": sec["kode"], "description": sec.get("beskrivelse")},
              ev("institusjonellSektorkode"))
    act = " ".join(body.get("aktivitet") or [])
    purpose = " ".join(body.get("vedtektsfestetFormaal") or [])
    if act or purpose:
        b.put("business_description", {k: v for k, v in (("activity", act), ("statutory_purpose", purpose)) if v},
              ev("aktivitet", "vedtektsfestetFormaal"))
    n = body.get("antallAnsatte")
    as_of = body.get("registreringsdatoAntallAnsatteEnhetsregisteret")
    if _num(n) and body.get("harRegistrertAntallAnsatte") is not False:
        b.put("registered_employees", {"count": n, "as_of": as_of} if as_of else {"count": n},
              ev("antallAnsatte", "harRegistrertAntallAnsatte", "registreringsdatoAntallAnsatteEnhetsregisteret"),
              note="Employee count as reported to the register (Aa-registeret); it can lag real headcount.")
    elif body.get("harRegistrertAntallAnsatte") is True:
        b.put("registered_employees", {"range": "0-4"}, ev("harRegistrertAntallAnsatte"), confidence=0.9,
              note="The register withholds exact counts below 5; an employee count is registered but not shown.")
    else:
        b.absent("registered_employees", "not_available",
                 "No employee count is registered for this entity (not the same as zero employees).")
    if isinstance(body.get("registrertIMvaregisteret"), bool):
        v = {"registered": body["registrertIMvaregisteret"]}
        if body.get("registreringsdatoMerverdiavgiftsregisteret"):
            v["since"] = body["registreringsdatoMerverdiavgiftsregisteret"]
        b.put("vat_registered", v, ev("registrertIMvaregisteret", "registreringsdatoMerverdiavgiftsregisteret"))
    if isinstance(body.get("registrertIForetaksregisteret"), bool):
        v = {"registered": body["registrertIForetaksregisteret"]}
        if body.get("registreringsdatoForetaksregisteret"):
            v["since"] = body["registreringsdatoForetaksregisteret"]
        b.put("business_register", v, ev("registrertIForetaksregisteret", "registreringsdatoForetaksregisteret"))
    cap = body.get("kapital")
    if isinstance(cap, dict) and _num(cap.get("belop")):
        b.put("share_capital", {k: v for k, v in (("amount", cap.get("belop")), ("currency", cap.get("valuta")),
                                                  ("shares", cap.get("antallAksjer")), ("type", cap.get("type")),
                                                  ("as_of", cap.get("innfortDato"))) if _has(v)}, ev("kapital"))
    if isinstance(body.get("erIKonsern"), bool):
        b.put("group_membership", body["erIKonsern"], ev("erIKonsern"),
              note="True means the register marks the company as part of a corporate group (konsern).")
    if isinstance(body.get("historiskeNavn"), list):
        names = [{"name": h.get("navn"), "from": (h.get("fraDato") or "")[:10] or None,
                  "to": (h.get("tilDato") or "")[:10] or None} for h in body["historiskeNavn"] if isinstance(h, dict)]
        b.put("former_names", names, ev("historiskeNavn"))
    if s.kind == "underenhet" and body.get("overordnetEnhet"):
        b.put("parent_unit", body["overordnetEnhet"], ev("overordnetEnhet"),
              note="This number is an establishment (underenhet). The parent's facts are not merged into it.")
    if _has(body.get("sisteInnsendteAarsregnskap")):
        b.put("latest_filed_accounts_year", body["sisteInnsendteAarsregnskap"], ev("sisteInnsendteAarsregnskap"))
    if _has(body.get("hjemmeside")):
        b.put("registered_website", body["hjemmeside"], ev("hjemmeside"),
              note="As declared to the register; see official_website for the verification result.")
    else:
        b.absent("registered_website", "not_available", "No website is declared in Enhetsregisteret.")
    contact = {k: body[src] for k, src in (("email", "epostadresse"), ("phone", "telefon"), ("mobile", "mobil"))
               if _has(body.get(src))}
    if contact:
        b.put("registered_contact", contact, ev("epostadresse", "telefon", "mobil"))
    else:
        b.absent("registered_contact", "not_available", "No email or phone is registered.")
    a = _addr(body.get("forretningsadresse") or body.get("beliggenhetsadresse"))
    if a:
        b.put("business_address", a, ev("forretningsadresse" if body.get("forretningsadresse") else "beliggenhetsadresse"))
    else:
        b.absent("business_address", "not_available", "No business address in the register.")
    a = _addr(body.get("postadresse"))
    if a:
        b.put("postal_address", a, ev("postadresse"))
    else:
        b.absent("postal_address", "not_available", "No separate postal address is registered.")


def _financials(b: Builder, s: CompanyState) -> None:
    fields = ["annual_accounts_latest", "revenue", "operating_result", "profit_before_tax", "net_result",
              "total_assets", "equity", "total_liabilities", "personnel_costs", "annual_accounts_history",
              "revenue_change"]
    st = s.status.get("accounts")
    f = s.accounts
    if f is None or st is None or st.state != "checked":
        if st is not None and st.state == "not_found":
            reason = ("No accounts were returned by the open Regnskapsregisteret API (HTTP 404). It serves filers "
                      "using the standard layouts; banks, insurers and some others are not covered.")
            av = "not_available"
        else:
            msg = None
            if f is not None and isinstance(f.data, dict):
                msg = f.data.get("message")
            av, reason = _status_reason(st, "Annual accounts (Regnskapsregisteret)")
            if msg:
                av, reason = "not_available", f"The open accounts API cannot serve this entity's accounts ({msg})."
        for fld in fields:
            b.absent(fld, av, reason)
        return
    items = f.data if isinstance(f.data, list) else []
    mine = [(i, e) for i, e in enumerate(items)
            if isinstance(e, dict) and (e.get("virksomhet") or {}).get("organisasjonsnummer") == s.orgnr]
    if not mine:
        for fld in fields:
            b.absent(fld, "not_available", "No accounts for this organisation number in the open accounts data.")
        return
    periods: dict[str, list] = {}
    for i, e in mine:
        end = ((e.get("regnskapsperiode") or {}).get("tilDato")) or ""
        if end:
            periods.setdefault(end, []).append((i, e))
    chosen: dict[str, tuple[int, dict]] = {}
    conflicts = []
    for end, grp in periods.items():
        best = min(TYPE_RANK.get(e.get("regnskapstype"), 2) for _, e in grp)
        cands = [(i, e) for i, e in grp if TYPE_RANK.get(e.get("regnskapstype"), 2) == best]
        sig = {canon([e.get("valuta")] + [jpath_get(e, p) for _, p in FIN]) for _, e in cands}
        if len(sig) > 1:
            conflicts.append(end)
            continue
        chosen[end] = cands[0]
    if not chosen:
        for fld in fields:
            b.absent(fld, "ambiguous", "Conflicting accounts records were filed for the same period; none published.")
        return
    ends = sorted(chosen, reverse=True)
    latest_end = ends[0]
    idx, e = chosen[latest_end]
    cur = e.get("valuta")
    per = e.get("regnskapsperiode") or {}
    rp = {"start": per.get("fraDato"), "end": per.get("tilDato")}
    if not cur:
        for fld in fields:
            b.absent(fld, "not_available", "The accounts record states no currency; figures were not published.")
        return
    atype = e.get("regnskapstype")
    pre = f"[{idx}]"
    base_ev = [b.ev_json(f.resp, f"{pre}.regnskapsperiode", "official_accounts", rp),
               b.ev_json(f.resp, f"{pre}.valuta", "official_accounts", rp)]
    if atype:
        base_ev.append(b.ev_json(f.resp, f"{pre}.regnskapstype", "official_accounts", rp))
    figures, fig_ev = {}, list(base_ev)
    group_note = ("Consolidated group (KONSERN) figures, not the company alone. " if atype == "KONSERN" else "")
    for fld, path in FIN:
        v = jpath_get(e, path)
        if _num(v):
            eid = b.ev_json(f.resp, f"{pre}.{path}", "official_accounts", rp)
            figures[fld] = v
            fig_ev.append(eid)
            b.put(fld, {"amount": v, "currency": cur, "period_start": rp["start"], "period_end": rp["end"],
                        "accounts_type": atype}, base_ev + [eid], reporting_period=rp,
                  note=group_note + "Copied unchanged from the filed annual accounts; no conversion or rounding.")
        else:
            b.absent(fld, "not_available", f"Not reported in the open accounts record for the period ending {rp['end']}.")
    rules = jpath_get(e, "regnkapsprinsipper") or {}
    rev = e.get("revisjon") or {}
    latest = {"period_start": rp["start"], "period_end": rp["end"], "currency": cur, "accounts_type": atype,
              "figures": figures, "small_company_rules": rules.get("smaaForetak"),
              "audit_opted_out": rev.get("fravalgRevisjon"), "liquidation_accounts": e.get("avviklingsregnskap")}
    b.put("annual_accounts_latest", {k: v for k, v in latest.items() if v is not None}, fig_ev, reporting_period=rp,
          note=(group_note + (f"Conflicting filings for {', '.join(conflicts)} were not published. " if conflicts else "")).strip() or None)
    hist, hist_ev = [], []
    for end in ends:
        i2, e2 = chosen[end]
        per2 = e2.get("regnskapsperiode") or {}
        row = {"period_start": per2.get("fraDato"), "period_end": end, "currency": e2.get("valuta"),
               "accounts_type": e2.get("regnskapstype")}
        rp2 = {"start": per2.get("fraDato"), "end": end}
        for fld, path in FIN[:7]:
            v = jpath_get(e2, path)
            if _num(v):
                row[fld] = v
                hist_ev.append(b.ev_json(f.resp, f"[{i2}].{path}", "official_accounts", rp2))
        hist_ev.append(b.ev_json(f.resp, f"[{i2}].regnskapsperiode", "official_accounts", rp2))
        hist.append(row)
    b.put("annual_accounts_history", hist, hist_ev,
          note=f"{len(hist)} period(s) available in the open accounts API, newest first.")
    if len(ends) >= 2:
        i0, e0 = chosen[ends[1]]
        a1, a0 = jpath_get(e, FIN[0][1]), jpath_get(e0, FIN[0][1])
        if _num(a1) and _num(a0) and e0.get("valuta") == cur and e0.get("regnskapstype") == atype:
            ids = [b.ev_json(f.resp, f"{pre}.{FIN[0][1]}", "official_accounts", rp),
                   b.ev_json(f.resp, f"[{i0}].{FIN[0][1]}", "official_accounts",
                             {"start": (e0.get("regnskapsperiode") or {}).get("fraDato"), "end": ends[1]})]
            pct = None if a0 == 0 else round((a1 - a0) / abs(a0) * 100, 1)
            b.put("revenue_change", {"from_period_end": ends[1], "to_period_end": latest_end, "from": a0, "to": a1,
                                     "change": round(a1 - a0, 2), "change_pct": pct, "currency": cur},
                  ids, confidence=0.97, reporting_period=rp, derived_from=ids,
                  note="Computed from the two filed revenue figures cited; percentage rounded to one decimal.")
        else:
            b.absent("revenue_change", "not_available", "Revenue is not available for two comparable periods.")
    else:
        b.absent("revenue_change", "not_available", "Only one accounting period is available in the open data.")


ROLE_GROUP = {"DAGL": "chief_executive", "LEDE": "board_chair", "REVI": "auditor", "REGN": "accountant"}
BOARD = {"LEDE", "NEST", "MEDL"}


def _people(b: Builder, s: CompanyState) -> None:
    fields = ["leadership", "chief_executive", "board_chair", "board_members", "auditor", "accountant"]
    if s.kind == "underenhet":
        for fld in fields:
            b.absent(fld, "not_applicable", "Establishments have no roles of their own; see the parent unit.")
        return
    st = s.status.get("roles")
    f = s.roles
    if f is None or st is None or st.state != "checked" or not isinstance(f.data, dict):
        av, reason = _status_reason(st, "Roles (Enhetsregisteret)")
        for fld in fields:
            b.absent(fld, av, reason)
        return
    roles, by_code = [], {}
    for gi, grp in enumerate(f.data.get("rollegrupper") or []):
        gname = (grp.get("type") or {}).get("beskrivelse")
        for ri, r in enumerate(grp.get("roller") or []):
            if r.get("fratraadt") or r.get("avregistrert"):
                continue
            t = r.get("type") or {}
            base = f"rollegrupper[{gi}].roller[{ri}]"
            item = {"role_code": t.get("kode"), "role": t.get("beskrivelse"), "group": gname}
            if isinstance(r.get("person"), dict):
                nm = r["person"].get("navn") or {}
                item["holder_type"] = "person"
                item["name"] = " ".join(x for x in (nm.get("fornavn"), nm.get("mellomnavn"), nm.get("etternavn")) if x)
                ids = [b.ev_json(f.resp, f"{base}.person.navn"), b.ev_json(f.resp, f"{base}.type")]
            elif isinstance(r.get("enhet"), dict):
                en = r["enhet"]
                nm = en.get("navn")
                item["holder_type"] = "organisation"
                item["name"] = " ".join(nm) if isinstance(nm, list) else nm
                item["holder_orgnr"] = en.get("organisasjonsnummer")
                ids = [b.ev_json(f.resp, f"{base}.enhet.organisasjonsnummer"), b.ev_json(f.resp, f"{base}.type")]
                if en.get("navn"):
                    ids.append(b.ev_json(f.resp, f"{base}.enhet.navn"))
            else:
                continue
            if not item.get("name"):
                continue
            item = {k: v for k, v in item.items() if _has(v)}
            roles.append((item, ids))
            by_code.setdefault(item.get("role_code"), []).append((item, ids))
    if not roles:
        for fld in fields:
            b.absent(fld, "not_available", "No current role holders are registered for this entity.")
        return
    b.put("leadership", [r for r, _ in roles], [i for _, ids in roles for i in ids],
          note="Current roles only (resigned or deregistered excluded). Birth dates are never copied.")
    for code, fld in ROLE_GROUP.items():
        hits = by_code.get(code) or []
        if code == "DAGL" and not hits:
            hits = by_code.get("INNH") or []  # sole proprietorship: the owner runs it
        if hits:
            val = [h for h, _ in hits]
            b.put(fld, val[0] if len(val) == 1 else val, [i for _, ids in hits for i in ids])
        else:
            b.absent(fld, "not_available", f"No current {fld.replace('_', ' ')} is registered.")
    board = [(r, ids) for r, ids in roles if r.get("role_code") in BOARD]
    if board:
        b.put("board_members", [r for r, _ in board], [i for _, ids in board for i in ids])
    else:
        b.absent("board_members", "not_available", "No board is registered (common for sole proprietorships).")


def _locations(b: Builder, s: CompanyState) -> None:
    if s.kind == "underenhet":
        b.absent("registered_workplaces", "not_applicable", "This number is itself an establishment.")
        return
    st = s.status.get("subunits")
    f = s.subunits
    if f is None or st is None or st.state != "checked" or not isinstance(f.data, dict):
        av, reason = _status_reason(st, "Establishments (Enhetsregisteret)")
        b.absent("registered_workplaces", av, reason)
        return
    emb = (f.data.get("_embedded") or {}).get("underenheter") or []
    total = (f.data.get("page") or {}).get("totalElements", len(emb))
    items, ids = [], []
    for i, u in enumerate(emb):
        if not isinstance(u, dict) or u.get("overordnetEnhet") != s.orgnr:
            continue  # a foreign establishment never leaks into this profile
        it = {"orgnr": u.get("organisasjonsnummer"), "name": u.get("navn"),
              "address": _addr(u.get("beliggenhetsadresse")), "started": u.get("oppstartsdato"),
              "closed": u.get("nedleggelsesdato")}
        if _num(u.get("antallAnsatte")) and u.get("harRegistrertAntallAnsatte") is not False:
            it["employees"] = u["antallAnsatte"]
        items.append({k: v for k, v in it.items() if _has(v)})
        ids.append(b.ev_json(f.resp, f"_embedded.underenheter[{i}].organisasjonsnummer"))
        if u.get("beliggenhetsadresse"):
            ids.append(b.ev_json(f.resp, f"_embedded.underenheter[{i}].beliggenhetsadresse"))
    ids.append(b.ev_json(f.resp, "page.totalElements"))
    foreign = sum(1 for u in emb if isinstance(u, dict) and u.get("overordnetEnhet") != s.orgnr)
    total = max(len(items), int(total or 0) - foreign)
    note = None
    if total > len(items):
        note = f"The register lists {total} establishments; the first {len(items)} are included."
    b.put("registered_workplaces", {"total": total, "items": items}, ids, note=note or
          ("The register lists no establishments for this entity." if not items else None))


EVENT_CAP = 15


def _activity(b: Builder, s: CompanyState, window_days: int) -> list[dict]:
    """Registry events and role events. Returns activity items for the merged timeline."""
    timeline: list[dict] = []
    events = []
    emp_seen = False
    for f in sorted(s.updates, key=lambda x: (x.data or {}).get("dato", ""), reverse=True):
        ev = f.data or {}
        date = (ev.get("dato") or "")[:10]
        changes = []
        for ch in ev.get("endringer") or []:
            path = (ch.get("path") or "").lstrip("/")
            top = path.split("/")[0]
            if top in NOISE_PATHS:
                continue
            label = CHANGE_LABELS.get(top, f"{top} changed")
            if top == "sisteInnsendteAarsregnskap" and ch.get("value"):
                label = f"annual accounts for {ch['value']} registered"
            elif top == "antallAnsatte" and _num(ch.get("value")):
                label = f"registered employee count updated to {int(ch['value'])}"
            changes.append(label)
        if not changes and ev.get("endringstype") not in ("Ny", "Sletting"):
            continue  # an update without field detail adds nothing a reader can use
        only_emp = changes and all(c.startswith("registered employee count") for c in changes)
        if only_emp and emp_seen:
            continue  # keep the newest headcount update, drop the monthly repeats
        emp_seen = emp_seen or bool(only_emp)
        typ = {"Ny": "registered", "Sletting": "deleted"}.get(ev.get("endringstype"), "changed")
        eid = b.ev_json(f.resp, f.path, source_url=f"{BRREG}/oppdateringer/enheter?organisasjonsnummer={s.orgnr}")
        item = {"date": date, "type": typ, "changes": changes or [typ]}
        events.append((item, eid))
        if len(events) >= EVENT_CAP:
            break
    st = s.status.get("entity_events")
    if events:
        b.put("registry_change_events", [e for e, _ in events], [i for _, i in events],
              note=f"From the register's change feed (last {window_days // 365} years); repetitive monthly "
                   "headcount refreshes are collapsed to the newest one.")
        for e, i in events:
            timeline.append({"date": e["date"], "kind": "registry_change", "title": "; ".join(e["changes"]).capitalize(),
                             "source": "Enhetsregisteret", "evidence_ids": [i]})
    else:
        av, reason = _status_reason(st, "Register change feed")
        if st and st.state == "checked":
            av, reason = "not_available", f"No field-level changes were published in the last {window_days} days."
        b.absent("registry_change_events", av, reason)
    rev = []
    for f in sorted(s.role_events, key=lambda x: (x.data or {}).get("time", ""), reverse=True)[:10]:
        d = (f.data or {}).get("time", "")[:10]
        rev.append(({"date": d, "type": "roles updated"},
                    b.ev_json(f.resp, f.path, source_url=f"{BRREG}/oppdateringer/roller?organisasjonsnummer={s.orgnr}")))
    st = s.status.get("role_events")
    if rev:
        b.put("role_change_events", [x for x, _ in rev], [i for _, i in rev],
              note="The register signals that roles (board, CEO, auditor...) were updated on these dates; "
                   "see leadership for the current holders.")
        for x, i in rev:
            timeline.append({"date": x["date"], "kind": "leadership_update", "title": "Roles updated in the register",
                             "source": "Enhetsregisteret", "evidence_ids": [i]})
    else:
        av, reason = _status_reason(st, "Role change feed")
        if st and st.state == "checked":
            av, reason = "not_available", f"No role updates were published in the last {window_days} days."
        b.absent("role_change_events", av, reason)
    # Former names with an end date inside the window are dated events too.
    rec = s.record
    for j, h in enumerate((s.body().get("historiskeNavn") or [])):
        to = (h or {}).get("tilDato") or ""
        if to[:4].isdigit() and int(to[:4]) >= 2000 and rec is not None:
            timeline.append({"date": to[:10], "kind": "name_change", "title": f"Changed name from {h.get('navn')}",
                             "source": "Enhetsregisteret",
                             "evidence_ids": [b.ev_json(rec.resp, _p(rec.path, f"historiskeNavn[{j}]"),
                                                        source_url=record_url(s))]})
    return timeline


def _web(b: Builder, s: CompanyState, timeline: list[dict]) -> None:
    w = s.web or {}
    st = s.status.get("website")
    v = w.get("verified")
    no_site_fields = ["social_profiles", "website_contact", "website_locations", "public_brand_name",
                      "website_description", "website_news", "careers_page"]
    if not v:
        if st is None:
            av, reason = "failed", "Website stage did not run."
        elif st.state == "ambiguous":
            av, reason = "ambiguous", f"Candidate website not attributed: {st.detail}"
        elif st.state == "not_found":
            av, reason = "not_available", f"No official website found: {st.detail}"
        else:
            av, reason = _status_reason(st, "Official website")
        val = None
        if av == "ambiguous":
            val = [a.get("candidate") for a in w.get("attempts", []) if a.get("verdict") in ("other_entity", "unconfirmed")]
        b.absent("official_website", av, reason, value=val,
                 checked_sources=[{"candidate": a.get("candidate"), "origin": a.get("origin"),
                                   "outcome": a.get("verdict"), "detail": a.get("detail")}
                                  for a in w.get("attempts", [])] or None)
        derived = "not_available" if av in ("not_available", "ambiguous") else av
        for fld in no_site_fields:
            b.absent(fld, derived, "No verified official website to read company-owned information from.")
        return
    home = v["home"]
    proof_id = b.ev_text(v["proof"], v["span"], f"identity proof: {v['method']}")
    ids = [proof_id]
    if v["origin"] == "registry" and s.record:
        ids.append(b.ev_json(s.record.resp, _p(s.record.path, "hjemmeside"), source_url=record_url(s)))
    if v["origin"] == "nav_employer_homepage":
        for f in s.nav_ads:
            if ((f.data or {}).get("employer") or {}).get("homepage"):
                ids.append(b.ev_json(f.resp, "ad_content.employer.homepage", "public_job_feed"))
                break
    p = urlsplit(home.final_url)
    b.put("official_website", {"url": f"{p.scheme}://{p.netloc}/", "final_url": home.final_url,
                               "verification": v["method"], "candidate_from": v["origin"]},
          ids, confidence=v["confidence"],
          note="Published only after the identity gate passed (see evidence span).")
    # brand + description
    if w.get("brand"):
        sp = _raw_span(home, w["brand"])
        if sp:
            b.put("public_brand_name", w["brand"], [b.ev_text(home, sp, "og:site_name / JSON-LD Organization.name")],
                  confidence=0.9)
    if not b.has("public_brand_name"):
        b.absent("public_brand_name", "not_available", "The verified website does not declare a brand name in "
                                                       "structured data.")
    if w.get("description"):
        sp = _raw_span(home, w["description"][:80])
        if sp:
            b.put("website_description", w["description"], [b.ev_text(home, sp, "meta description")], confidence=0.9,
                  note="The company's own description of itself (marketing copy, not independent evidence).")
    if not b.has("website_description"):
        b.absent("website_description", "not_available", "The verified website has no meta description.")
    # profiles
    profiles, pids = [], []
    for platform, items in sorted((w.get("socials") or {}).items()):
        for it in items:
            url = it["url"]
            needle = urlsplit(url).netloc.replace("www.", "") + urlsplit(url).path
            sp = _raw_span(it["page"], needle) or _raw_span(it["page"], urlsplit(url).path)
            if not sp:
                continue
            profiles.append({"platform": platform, "url": url})
            pids.append(b.ev_text(it["page"], sp, "outbound link on verified website"))
    if profiles:
        b.put("social_profiles", profiles, pids, confidence=0.95,
              note="Linked from the company's verified website; profile contents were not collected.")
    else:
        b.absent("social_profiles", "not_available", "The verified website links to no company profiles.")
    # contact
    cont, cids = {}, []
    for e, page in w.get("emails") or []:
        sp = _raw_span(page, "mailto:" + e)
        if sp:
            cont.setdefault("emails", []).append(e)
            cids.append(b.ev_text(page, sp, "mailto link"))
    for ph, (page, href) in w.get("phones") or []:
        sp = _raw_span(page, href)
        if sp:
            cont.setdefault("phones", []).append(ph)
            cids.append(b.ev_text(page, sp, "tel link"))
    if cont:
        b.put("website_contact", cont, cids, confidence=0.9)
    else:
        b.absent("website_contact", "not_available", "No mailto/tel links on the pages read.")
    org = w.get("org_jsonld")
    if w.get("addresses") and org:
        sp = _raw_span(org[1], '"address"') or _raw_span(org[1], "address")
        if sp:
            b.put("website_locations", w["addresses"], [b.ev_text(org[1], sp, "JSON-LD Organization.address")],
                  confidence=0.9)
    if not b.has("website_locations"):
        b.absent("website_locations", "not_available", "The verified website publishes no structured address.")
    # news
    news, nids = [], []
    for n in w.get("news") or []:
        sp = _raw_span(n["page"], n["title"][:60]) or _raw_span(n["page"], urlsplit(n["url"]).path)
        if not sp:
            continue
        eid = b.ev_text(n["page"], sp, n["method"])
        news.append({"date": n["date"], "title": n["title"], "url": n["url"]})
        nids.append(eid)
        timeline.append({"date": n["date"], "kind": "website_news", "title": n["title"], "url": n["url"],
                         "source": "Official website", "evidence_ids": [eid]})
    if news:
        b.put("website_news", news, nids, confidence=0.88,
              note="Dated items published on the company's verified website (company-reported).")
    else:
        b.absent("website_news", "not_available", "No dated news found on the verified website (RSS, "
                                                  "structured data or dated news list).")
    # careers page
    c = w.get("careers")
    if c is not None:
        sp = _raw_span(c, "<title") or c.text()[:200]
        b.put("careers_page", c.final_url, [b.ev_text(c, sp, "careers page fetched", "company_owned" if
                                                      urlsplit(c.final_url).netloc.endswith(urlsplit(home.final_url).netloc.replace("www.", ""))
                                                      else "company_linked")], confidence=0.9)
    else:
        b.absent("careers_page", "not_available", w.get("careers_status") or "No careers page found.")


def _jobs(b: Builder, s: CompanyState, timeline: list[dict]) -> None:
    posts, ids = [], []
    for f in s.nav_ads:
        ad = f.data or {}
        emp = ad.get("employer") or {}
        locs = ["/".join(x for x in (l.get("city"), l.get("municipal")) if x) for l in ad.get("workLocations") or []
                if isinstance(l, dict)]
        aid = [b.ev_json(f.resp, "ad_content.title", "public_job_feed"),
               b.ev_json(f.resp, "ad_content.employer.orgnr", "public_job_feed"),
               b.ev_json(f.resp, "ad_content.published", "public_job_feed")]
        item = {"title": ad.get("title"), "employer": emp.get("name"), "employer_orgnr": emp.get("orgnr"),
                "relation": f.path, "location": "; ".join(x for x in locs if x) or None,
                "published": (ad.get("published") or "")[:10] or None, "expires": (ad.get("expires") or "")[:10] or None,
                "application_due": ad.get("applicationDue") or None, "positions": ad.get("positioncount") or None,
                "url": ad.get("link") or f"https://arbeidsplassen.nav.no/stillinger/stilling/{ad.get('uuid')}",
                "source": "NAV job vacancy feed"}
        posts.append({k: v for k, v in item.items() if v is not None})
        ids.extend(aid)
        if item["published"]:
            timeline.append({"date": item["published"], "kind": "job_posted", "title": f"Job posted: {item['title']}",
                             "url": item["url"], "source": "NAV", "evidence_ids": aid})
    w = s.web or {}
    seen = {re.sub(r"\W+", "", (p.get("title") or "").lower()) for p in posts}
    for j in w.get("jobs") or []:
        key = re.sub(r"\W+", "", j["title"].lower())
        if key in seen:
            continue
        page = j["page"]
        sp = _raw_span(page, j["title"][:60]) or (_raw_span(page, urlsplit(j["url"]).path) if j.get("url") else None)
        if not sp:
            continue
        seen.add(key)
        sc = "company_owned"
        if (w.get("verified") or {}).get("home") is not None:
            own = urlsplit(w["verified"]["home"].final_url).netloc.replace("www.", "")
            if not urlsplit(page.final_url).netloc.endswith(own):
                sc = "company_linked"
        eid = b.ev_text(page, sp, j["method"], sc)
        item = {"title": j["title"], "url": j.get("url"), "location": j.get("location"),
                "published": j.get("date_posted"), "expires": j.get("valid_through"),
                "source": "Official website careers page" if sc == "company_owned" else "Recruitment page linked from the official website"}
        posts.append({k: v for k, v in item.items() if v})
        ids.append(eid)
        if j.get("date_posted"):
            timeline.append({"date": j["date_posted"], "kind": "job_posted", "title": f"Job posted: {j['title']}",
                             "url": j.get("url"), "source": "Official website", "evidence_ids": [eid]})
    nav = s.status.get("nav_jobs") or SourceStatus("not_checked")
    site_state = ("checked" if w.get("careers") is not None else
                  ("no verified website" if not w.get("verified") else w.get("careers_status") or "no careers page"))
    checked = [{"source": "NAV job vacancy feed (ads tied to this org number or its establishments)",
                "status": nav.state, "detail": nav.detail,
                "result": f"{sum(1 for p in posts if p.get('source') == 'NAV job vacancy feed')} active ad(s)"},
               {"source": "official website careers page", "status": site_state,
                "result": f"{sum(1 for p in posts if p.get('source') != 'NAV job vacancy feed')} posting(s)"}]
    if posts:
        posts.sort(key=lambda p: p.get("published") or "", reverse=True)
        conf = 0.97 if all(p.get("source") == "NAV job vacancy feed" for p in posts) else 0.85
        b.put("job_postings", posts, ids, confidence=conf,
              note="NAV ads are published only when the ad's own employer org number is this company or one of "
                   "its registered establishments. Contact persons are never copied.")
        b.put("hiring_signal", {"hiring": True, "open_postings": len(posts)}, ids, confidence=conf)
        return
    nv = w.get("no_vacancy")
    if nv:
        eid = b.ev_text(nv[0], _raw_span(nv[0], nv[1]) or nv[1], "explicit no-vacancy statement on careers page")
        b.put("job_postings", [], [eid], confidence=0.85,
              note="Checked: the company's careers page states that there are no open positions.")
        b.put("hiring_signal", {"hiring": False, "open_postings": 0}, [eid], confidence=0.8)
        return
    if nav.state == "checked" or site_state == "checked":
        reason = ("No open postings found in the sources checked. This is not proof that the company is not "
                  "hiring: ads on Finn.no and JavaScript-only career sites are not covered.")
        av = "not_available"
    else:
        av, reason = _status_reason(nav, "Job sources")
    b.absent("job_postings", av, reason, checked_sources=checked)
    b.absent("hiring_signal", av, reason, checked_sources=checked)


def build(s: CompanyState, window_days: int = 730) -> tuple[Builder, list[dict]]:
    b = Builder(s.orgnr or s.input)
    timeline: list[dict] = []
    if s.record is None:
        return b, timeline
    _identity(b, s)
    if s.identity.state == "deleted":
        return b, timeline
    if s.kind == "enhet":
        _financials(b, s)
    else:
        for fld in ("annual_accounts_latest", "revenue", "net_result", "equity", "total_assets",
                    "annual_accounts_history"):
            b.absent(fld, "not_applicable", "Establishments do not file accounts; see the parent unit.")
    _people(b, s)
    _locations(b, s)
    if s.kind == "enhet":
        timeline += _activity(b, s, window_days)
        _web(b, s, timeline)
        _jobs(b, s, timeline)
    timeline = [t for t in timeline if t.get("date")]
    timeline.sort(key=lambda t: (t["date"], t["kind"], t["title"]), reverse=True)
    if timeline:
        ids = sorted({i for t in timeline[:25] for i in t["evidence_ids"]})
        items = [{k: v for k, v in t.items() if k != "evidence_ids" and v is not None} for t in timeline[:25]]
        b.put("public_activity", items, ids, confidence=0.95,
              note="Merged, newest first: register changes, role updates, job postings and website news.")
    elif s.kind == "enhet":
        b.absent("public_activity", "not_available", "No dated public activity found in the sources checked.")
    return b, timeline
