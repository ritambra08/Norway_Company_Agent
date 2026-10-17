"""Pure extraction: saved API snapshots -> evidence-linked facts.

Rules that keep profiles safe:
  * a fact is only built from a record whose own organisasjonsnummer equals the
    requested one (wrong-company guard);
  * numbers are copied verbatim from the source, never computed or estimated;
  * a missing or ambiguous value becomes an entry in ``missing`` with a reason,
    never a guess.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .config import NO_ACCOUNTS_FORMS
from .http import Snapshot
from .models import Fact, Source

ENH = "Brønnøysundregistrene – Enhetsregisteret"
REG = "Brønnøysundregistrene – Regnskapsregisteret"
RECORD = "https://virksomhet.brreg.no/nb/oppslag/{kind}/{orgnr}"

FIN_FIELDS = [
    ("revenue", "Operating revenue", "resultatregnskapResultat.driftsresultat.driftsinntekter.sumDriftsinntekter"),
    ("operating_result", "Operating result", "resultatregnskapResultat.driftsresultat.driftsresultat"),
    ("pre_tax_result", "Result before tax", "resultatregnskapResultat.ordinaertResultatFoerSkattekostnad"),
    ("net_result", "Net result for the year", "resultatregnskapResultat.aarsresultat"),
    ("operating_costs", "Operating costs", "resultatregnskapResultat.driftsresultat.driftskostnad.sumDriftskostnad"),
    ("personnel_costs", "Personnel costs", "resultatregnskapResultat.driftsresultat.driftskostnad.loennskostnad"),
    ("equity", "Total equity", "egenkapitalGjeld.egenkapital.sumEgenkapital"),
    ("total_liabilities", "Total liabilities", "egenkapitalGjeld.gjeldOversikt.sumGjeld"),
    ("total_assets", "Total assets", "eiendeler.sumEiendeler"),
    ("current_assets", "Current assets", "eiendeler.omloepsmidler.sumOmloepsmidler"),
    ("fixed_assets", "Fixed assets", "eiendeler.anleggsmidler.sumAnleggsmidler"),
]

EXPECTED_ENHET = [
    "name", "organisation_form", "registration_date", "operating_status", "business_address",
    "nace_primary", "employees", "website", "email", "phone", "revenue", "net_result",
    "equity", "total_assets", "roles", "establishments",
]
EXPECTED_UNDERENHET = [
    "name", "organisation_form", "registration_date", "operating_status", "business_address",
    "nace_primary", "employees", "parent_unit_orgnr",
]


@dataclass
class Extraction:
    status: str
    entity_type: str | None
    facts: list[Fact] = field(default_factory=list)
    missing: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _has(v) -> bool:
    return v is not None and v != "" and v != [] and v != {}


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _dig(d, path: str):
    for part in path.split("."):
        if not isinstance(d, dict):
            return None
        d = d.get(part)
    return d


class _Builder:
    def __init__(self, orgnr: str, entity_type: str | None):
        self.orgnr = orgnr
        self.entity_type = entity_type
        self.facts: list[Fact] = []
        self.missing: list[dict] = []
        self.notes: list[str] = []

    def keys(self) -> set[str]:
        return {f.key for f in self.facts}

    def add(self, key, label, value, kind, snap, paths, explanation="", name=ENH, source_date=None):
        record_kind = "underenheter" if kind == "underenhet" else "enheter"
        self.facts.append(
            Fact(
                key=key,
                label=label,
                value=value,
                subject_orgnr=self.orgnr,
                explanation=explanation,
                source=Source(
                    name=name,
                    url=snap.url,
                    record_url=RECORD.format(kind=record_kind, orgnr=self.orgnr),
                    retrieved_at=snap.retrieved_at,
                    json_paths=list(paths),
                    source_date=source_date,
                    last_modified=snap.last_modified,
                    response_sha256=snap.sha256,
                    raw_file=f"raw/{self.orgnr}/{kind}.json",
                ),
            )
        )

    def miss(self, key: str, reason: str) -> None:
        if not any(m["key"] == key for m in self.missing):
            self.missing.append({"key": key, "reason": reason})


def _address(a) -> dict | None:
    if not isinstance(a, dict):
        return None
    out = {
        "lines": [x for x in (a.get("adresse") or []) if x],
        "postal_code": a.get("postnummer"),
        "city": a.get("poststed"),
        "municipality": a.get("kommune"),
        "municipality_number": a.get("kommunenummer"),
        "country": a.get("land"),
        "country_code": a.get("landkode"),
    }
    out = {k: v for k, v in out.items() if _has(v)}
    return out or None


def _code(obj) -> dict | None:
    if not isinstance(obj, dict) or not _has(obj.get("kode")):
        return None
    return {"code": obj.get("kode"), "description": obj.get("beskrivelse")}


def _entity_facts(b: _Builder, snap: Snapshot, kind: str, body: dict) -> None:
    def simple(key, label, path, explanation=""):
        if path in body and _has(body[path]):
            b.add(key, label, body[path], kind, snap, [path], explanation)

    simple("name", "Name", "navn", "Registered name in Enhetsregisteret.")

    form = _code(body.get("organisasjonsform"))
    if form:
        b.add("organisation_form", "Organisation form", form, kind, snap, ["organisasjonsform"],
              "Legal form code and description as registered.")

    simple("registration_date", "Registered in Enhetsregisteret", "registreringsdatoEnhetsregisteret")
    simple("commercial_register_date", "Registered in Foretaksregisteret on", "registreringsdatoForetaksregisteret")
    simple("vat_registration_date", "Registered in the VAT register on", "registreringsdatoMerverdiavgiftsregisteret")
    simple("founding_date", "Founding date", "stiftelsesdato")
    simple("language_form", "Language form", "maalform")
    simple("start_date", "Business start date", "oppstartsdato")
    simple("ownership_change_date", "Latest ownership change", "datoEierskifte")
    simple("closure_date", "Closure date", "nedleggelsesdato")
    simple("parent_unit_orgnr", "Parent unit (org. number)", "overordnetEnhet",
           "The main unit this establishment belongs to. Facts of the parent are NOT merged into this profile.")
    simple("last_submitted_annual_accounts_year", "Latest annual accounts submitted (year)",
           "sisteInnsendteAarsregnskap")
    simple("articles_purpose", "Purpose per articles of association", "vedtektsfestetFormaal")
    simple("activity_description", "Registered activity description", "aktivitet")

    for key, label, path in [
        ("registered_in_commercial_register", "Registered in Foretaksregisteret", "registrertIForetaksregisteret"),
        ("vat_registered", "Registered in the VAT register", "registrertIMvaregisteret"),
        ("registered_in_voluntary_register", "Registered in Frivillighetsregisteret", "registrertIFrivillighetsregisteret"),
        ("registered_in_foundation_register", "Registered in Stiftelsesregisteret", "registrertIStiftelsesregisteret"),
    ]:
        if isinstance(body.get(path), bool):
            b.add(key, label, body[path], kind, snap, [path])

    simple("website", "Website", "hjemmeside", "Website as registered by the entity; not independently verified.")
    simple("email", "Email", "epostadresse", "Contact email as registered by the entity.")
    simple("phone", "Phone", "telefon", "Contact phone as registered by the entity.")
    simple("mobile", "Mobile", "mobil", "Contact mobile as registered by the entity.")

    for key, label, paths in [
        ("business_address", "Business address", ["forretningsadresse", "beliggenhetsadresse"]),
        ("postal_address", "Postal address", ["postadresse"]),
    ]:
        for p in paths:
            addr = _address(body.get(p))
            if addr:
                b.add(key, label, addr, kind, snap, [p])
                break

    for key, label, path in [
        ("nace_primary", "Primary industry (NACE)", "naeringskode1"),
        ("nace_secondary", "Secondary industry (NACE)", "naeringskode2"),
        ("nace_tertiary", "Tertiary industry (NACE)", "naeringskode3"),
        ("institutional_sector", "Institutional sector", "institusjonellSektorkode"),
    ]:
        v = _code(body.get(path))
        if v:
            b.add(key, label, v, kind, snap, [path])

    n = body.get("antallAnsatte")
    if _num(n) and body.get("harRegistrertAntallAnsatte") is not False:
        b.add("employees", "Employees (registered)", n, kind, snap,
              ["antallAnsatte", "harRegistrertAntallAnsatte"],
              "Employee count as reported to the register; it can lag real headcount.")
    elif body.get("harRegistrertAntallAnsatte") is False:
        b.miss("employees", "The entity has not registered an employee count (not the same as zero employees).")

    # Operating status: a deterministic summary of the register's own flags.
    flags = ("konkurs", "underAvvikling", "underTvangsavviklingEllerTvangsopplosning")
    if any(isinstance(body.get(f), bool) for f in flags):
        if body.get("konkurs"):
            status = "bankrupt"
        elif body.get("underTvangsavviklingEllerTvangsopplosning"):
            status = "under_forced_dissolution"
        elif body.get("underAvvikling"):
            status = "under_liquidation"
        else:
            status = "active_in_register"
        b.add("operating_status", "Status in register", status, kind, snap, [f for f in flags if f in body],
              "Derived only from the register's konkurs / underAvvikling / underTvangsavvikling flags. "
              "'active_in_register' means none of those flags is set.")
    elif kind == "underenhet":
        status = "closed" if _has(body.get("nedleggelsesdato")) else "active_in_register"
        b.add("operating_status", "Status in register", status, kind, snap, ["nedleggelsesdato"],
              "Derived from whether a closure date is registered for the establishment.")


def _roles(b: _Builder, snap: Snapshot) -> None:
    body = snap.body if isinstance(snap.body, dict) else {}
    items = []
    for grp in body.get("rollegrupper") or []:
        group = (grp.get("type") or {}).get("beskrivelse")
        for r in grp.get("roller") or []:
            if r.get("fratraadt") or r.get("avregistrert"):
                continue  # only current role holders
            t = r.get("type") or {}
            item = {"role_code": t.get("kode"), "role": t.get("beskrivelse"), "group": group}
            if isinstance(r.get("person"), dict):
                n = r["person"].get("navn") or {}
                item["holder_type"] = "person"
                item["name"] = " ".join(x for x in (n.get("fornavn"), n.get("mellomnavn"), n.get("etternavn")) if x)
                # Birth dates are deliberately never copied.
            elif isinstance(r.get("enhet"), dict):
                e = r["enhet"]
                nm = e.get("navn")
                item["holder_type"] = "organization"
                item["name"] = " ".join(nm) if isinstance(nm, list) else nm
                item["holder_orgnr"] = e.get("organisasjonsnummer")
            else:
                continue
            if item.get("name"):
                items.append({k: v for k, v in item.items() if _has(v)})
    if items:
        b.add("roles", "Current role holders", items, "roller", snap, ["rollegrupper"],
              "Current roles only (resigned or deregistered excluded). Role holders that are organisations "
              "(e.g. auditor) keep their own org number and are not merged into this profile.")
    elif snap.status == 200:
        b.miss("roles", "No current role holders are registered for this entity.")
    else:
        b.miss("roles", f"Roles endpoint returned HTTP {snap.status}.")


def _subunits(b: _Builder, snap: Snapshot) -> None:
    if snap.status != 200 or not isinstance(snap.body, dict):
        b.miss("establishments", f"Establishments lookup returned HTTP {snap.status}.")
        return
    emb = (snap.body.get("_embedded") or {}).get("underenheter") or []
    total = (snap.body.get("page") or {}).get("totalElements", len(emb))
    items = []
    for u in emb:
        if u.get("overordnetEnhet") != b.orgnr:
            continue  # a foreign establishment must never leak into this profile
        it = {
            "orgnr": u.get("organisasjonsnummer"),
            "name": u.get("navn"),
            "municipality": (u.get("beliggenhetsadresse") or {}).get("kommune"),
        }
        if _num(u.get("antallAnsatte")) and u.get("harRegistrertAntallAnsatte") is not False:
            it["employees"] = u["antallAnsatte"]
        items.append({k: v for k, v in it.items() if _has(v)})
    dropped = len(emb) - len(items)
    if dropped:
        b.notes.append(f"Discarded {dropped} establishment record(s) whose parent was not {b.orgnr}.")
    b.add("establishments", "Establishments (underenheter)",
          {"total": total, "listed": len(items), "items": items}, "underenheter_list", snap,
          ["page.totalElements", "_embedded.underenheter"],
          "Each establishment has its own org number; it is listed here only as a child of this unit.")


def _as_list(body) -> list:
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        for k in ("regnskap", "content", "items"):
            if isinstance(body.get(k), list):
                return body[k]
        if "virksomhet" in body:
            return [body]
    return []


TYPE_RANK = {"SELSKAP": 0, "KONSERN": 1}  # company's own accounts before consolidated group accounts


def _financials(b: _Builder, snap: Snapshot, last_year: str | None) -> None:
    if snap.status != 200:
        msg = snap.body.get("message") if isinstance(snap.body, dict) else None
        if snap.status == 500 and isinstance(msg, str):
            reason = f"The open accounts API cannot serve this entity's accounts ({msg})."
        else:
            reason = ("No annual accounts were returned by the open Regnskapsregisteret API "
                      f"(HTTP {snap.status}); it covers only companies using the ordinary accounts layout.")
        b.miss("financials", reason)
        return
    items = _as_list(snap.body)
    mine = [(i, e) for i, e in enumerate(items)
            if isinstance(e, dict) and (e.get("virksomhet") or {}).get("organisasjonsnummer") == b.orgnr]
    foreign = len(items) - len(mine)
    if foreign:
        b.notes.append(f"Discarded {foreign} accounts record(s) that did not belong to {b.orgnr}.")
    if not mine:
        b.miss("financials", "No annual accounts for this organisation number in the open Regnskapsregisteret data.")
        return

    def period(e):
        return e.get("regnskapsperiode") or {}

    def rank(e):
        return TYPE_RANK.get(e.get("regnskapstype"), 2)

    latest = max((period(e).get("tilDato") or "") for _, e in mine)
    if not latest:
        b.miss("financials", "Accounts records carry no period end date, so the latest cannot be identified.")
        return
    in_period = [(i, e) for i, e in mine if (period(e).get("tilDato") or "") == latest]
    best = min(rank(e) for _, e in in_period)
    group = [(i, e) for i, e in in_period if rank(e) == best]

    def sig(e):
        return (e.get("valuta"), period(e).get("fraDato"), tuple(_dig(e, p) for _, _, p in FIN_FIELDS))

    if any(sig(e) != sig(group[0][1]) for _, e in group[1:]):
        b.miss("financials", f"Several conflicting accounts records exist for the period ending {latest}; "
                             "none were published to avoid a wrong figure.")
        return

    idx, e = group[0]
    atype = e.get("regnskapstype")
    currency = e.get("valuta")
    if not currency:
        b.miss("financials", "The accounts record states no currency; figures were not published.")
        return
    if len(in_period) > len(group):
        b.notes.append(f"Accounts of another type were also filed for the period ending {latest}; "
                       f"the {atype} accounts were published.")
    if last_year and latest[:4] < last_year:
        b.notes.append(f"Enhetsregisteret says accounts for {last_year} were submitted, but the open "
                       f"Regnskapsregisteret data only has the period ending {latest}.")

    what = ("Consolidated group figures (not the company alone). " if atype == "KONSERN" else "")
    produced = False
    for key, label, path in FIN_FIELDS:
        v = _dig(e, path)
        if _num(v):
            value = {"amount": v, "currency": currency,
                     "period_start": period(e).get("fraDato"), "period_end": latest}
            if atype:
                value["accounts_type"] = atype
            b.add(key, label, value, "regnskap", snap,
                  [f"[{idx}].{path}", f"[{idx}].valuta", f"[{idx}].regnskapsperiode"],
                  what + "Copied unchanged from the filed annual accounts; no conversion or rounding.",
                  name=REG, source_date=latest)
            produced = True
        else:
            b.miss(key, "Not reported in the open accounts record (the filing may use another layout).")
    if produced:
        rules = _dig(e, "regnkapsprinsipper.regnskapsregler")  # spelled this way in the official spec
        b.add("latest_accounts_period", "Latest accounts in open data",
              {"period_start": period(e).get("fraDato"), "period_end": latest, "currency": currency,
               "accounts_type": atype, "accounting_rules": rules, "liquidation_accounts": e.get("avviklingsregnskap")},
              "regnskap", snap, [f"[{idx}].regnskapsperiode", f"[{idx}].valuta"],
              name=REG, source_date=latest)


def extract(orgnr: str, snaps: dict[str, Snapshot]) -> Extraction:
    enhet = snaps.get("enhet")
    if enhet is None:
        return Extraction("error", None, notes=["No response was collected."])

    def mismatch(s: Snapshot) -> bool:
        return not isinstance(s.body, dict) or s.body.get("organisasjonsnummer") != orgnr

    if enhet.status == 200:
        if mismatch(enhet):
            got = enhet.body.get("organisasjonsnummer") if isinstance(enhet.body, dict) else None
            return Extraction("error", None, notes=[f"Identity mismatch: asked for {orgnr}, record is {got}. Nothing published."])
        kind, main = "enhet", enhet
    elif enhet.status == 404:
        under = snaps.get("underenhet")
        if under is None:
            return Extraction("error", None, notes=["Establishment lookup did not run."])
        if under.status == 200:
            if mismatch(under):
                return Extraction("error", None, notes=[f"Identity mismatch for establishment lookup of {orgnr}. Nothing published."])
            kind, main = "underenhet", under
        elif under.status == 404:
            return Extraction("not_found", None, notes=["Not found as a main unit or as an establishment."])
        elif under.status == 410:
            return _deleted(orgnr, under, "underenhet")
        else:
            return Extraction("error", None, notes=[f"Establishment lookup returned HTTP {under.status}."])
    elif enhet.status == 410:
        return _deleted(orgnr, enhet, "enhet")
    else:
        return Extraction("error", None, notes=[f"Enhetsregisteret returned HTTP {enhet.status}."])

    b = _Builder(orgnr, kind)
    _entity_facts(b, main, kind, main.body)

    if kind == "enhet":
        if "roller" in snaps:
            _roles(b, snaps["roller"])
        if "underenheter_list" in snaps:
            _subunits(b, snaps["underenheter_list"])
        if "regnskap" in snaps:
            _financials(b, snaps["regnskap"], str(main.body.get("sisteInnsendteAarsregnskap") or "") or None)
        elif ((main.body.get("organisasjonsform") or {}).get("kode")) in NO_ACCOUNTS_FORMS:
            b.miss("financials", "This organisation form does not file annual accounts with Regnskapsregisteret.")
        expected = EXPECTED_ENHET
    else:
        for key in ("revenue", "net_result", "equity", "total_assets", "roles", "establishments"):
            b.miss(key, "Establishments (underenheter) have no accounts or roles of their own; see the parent unit.")
        expected = EXPECTED_UNDERENHET

    have = b.keys()
    for key in expected:
        if key not in have:
            b.miss(key, "Not present in the registry record for this entity.")
    b.missing = [m for m in b.missing if m["key"] not in have]
    return Extraction("ok", kind, b.facts, b.missing, b.notes)


def _deleted(orgnr: str, snap: Snapshot, kind: str) -> Extraction:
    body = snap.body if isinstance(snap.body, dict) else {}
    if body.get("organisasjonsnummer") != orgnr:
        return Extraction("error", None, notes=["Deleted-entity response did not match the requested org number."])
    b = _Builder(orgnr, kind)
    if _has(body.get("navn")):
        b.add("name", "Name (last registered)", body["navn"], kind, snap, ["navn"])
    if _has(body.get("slettedato")):
        b.add("deletion_date", "Deleted from register", body["slettedato"], kind, snap, ["slettedato"],
              "The register no longer lists this entity as active.")
    return Extraction("deleted", kind, b.facts, b.missing, ["Entity has been deleted from the register."])
