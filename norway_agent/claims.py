"""Claim and evidence objects for the Signalpost output contract.

Every published claim points at evidence; every piece of evidence points at an immutable snapshot
plus a locator (JSON path or verbatim text span) that ``validate`` can re-check.
"""

from __future__ import annotations

import hashlib
import json
import re

from . import PARSER_VERSION
from .http import Response

STATES = ("available", "not_available", "blocked", "not_applicable", "ambiguous", "failed")

SOURCES = {
    "official_registry": ("Brønnøysundregistrene – Enhetsregisteret", "NLOD 2.0", "open public API"),
    "official_accounts": ("Brønnøysundregistrene – Regnskapsregisteret", "NLOD 2.0", "open public API"),
    "public_job_feed": ("NAV – arbeidsplassen.nav.no job vacancy feed", "NAV API terms (arbeidsplassen.nav.no/vilkar-api)",
                        "official API with bearer token"),
    "company_owned": ("Company's verified official website", "company-published page; facts cited, not republished",
                      "robots.txt allowed"),
    "company_linked": ("Recruitment page linked from the verified website", "company-published page",
                       "robots.txt allowed"),
}

# field -> (family, label). Families map to the brief's sections.
FIELDS: dict[str, tuple[str, str]] = {
    "legal_name": ("identity", "Legal name"),
    "organisation_form": ("identity", "Legal form"),
    "registry_status": ("identity", "Status in the register"),
    "registration_date": ("identity", "Registered in Enhetsregisteret"),
    "founding_date": ("identity", "Founded"),
    "industry": ("identity", "Industry (NACE)"),
    "institutional_sector": ("identity", "Institutional sector"),
    "business_description": ("identity", "Registered activity and purpose"),
    "registered_employees": ("identity", "Employees (registered)"),
    "vat_registered": ("identity", "VAT registered"),
    "business_register": ("identity", "Registered in Foretaksregisteret"),
    "share_capital": ("identity", "Share capital"),
    "group_membership": ("identity", "Part of a corporate group"),
    "former_names": ("identity", "Former names"),
    "parent_unit": ("identity", "Parent unit (for establishments)"),
    "public_brand_name": ("identity", "Public brand name"),
    "website_description": ("identity", "Self-description on the official website"),
    "annual_accounts_latest": ("financials", "Latest annual accounts"),
    "revenue": ("financials", "Operating revenue"),
    "operating_result": ("financials", "Operating result"),
    "profit_before_tax": ("financials", "Result before tax"),
    "net_result": ("financials", "Net result"),
    "total_assets": ("financials", "Total assets"),
    "equity": ("financials", "Equity"),
    "total_liabilities": ("financials", "Total liabilities"),
    "personnel_costs": ("financials", "Personnel costs"),
    "annual_accounts_history": ("financials", "Annual accounts history"),
    "revenue_change": ("financials", "Revenue change vs previous year"),
    "latest_filed_accounts_year": ("financials", "Latest annual accounts submitted (year)"),
    "leadership": ("people", "Current roles"),
    "chief_executive": ("people", "Chief executive (daglig leder)"),
    "board_chair": ("people", "Chair of the board"),
    "board_members": ("people", "Board members"),
    "auditor": ("people", "Auditor"),
    "accountant": ("people", "External accountant"),
    "business_address": ("locations", "Business address"),
    "postal_address": ("locations", "Postal address"),
    "registered_workplaces": ("locations", "Registered workplaces (establishments)"),
    "website_locations": ("locations", "Addresses published on the official website"),
    "registered_contact": ("contact", "Contact details in the register"),
    "website_contact": ("contact", "Contact details on the official website"),
    "registered_website": ("website", "Website declared in the register"),
    "official_website": ("website", "Verified official website"),
    "social_profiles": ("profiles", "Company-owned profiles linked from the official website"),
    "careers_page": ("jobs", "Careers page"),
    "job_postings": ("jobs", "Open job postings"),
    "hiring_signal": ("jobs", "Is it hiring?"),
    "registry_change_events": ("activity", "Dated changes in the register"),
    "role_change_events": ("activity", "Dated leadership/role updates"),
    "website_news": ("activity", "Dated news on the official website"),
    "public_activity": ("activity", "Recent dated public activity"),
}

SECTION_OF_FAMILY = {"identity": "company_brief", "financials": "latest_financials", "people": "who_runs_it",
                     "locations": "where_it_operates", "contact": "where_it_operates", "website": "online_presence",
                     "profiles": "online_presence", "jobs": "working_here", "activity": "recent_activity"}


def canon(v) -> str:
    return json.dumps(v, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def jpath_get(obj, path: str):
    """'_embedded.enheter[3].navn' / '[2].valuta' -> value (or None)."""
    if not path:
        return obj
    cur = obj
    for part in re.findall(r"[^.\[\]]+|\[\d+\]", path):
        if part.startswith("["):
            i = int(part[1:-1])
            if not isinstance(cur, list) or i >= len(cur):
                return None
            cur = cur[i]
        else:
            if not isinstance(cur, dict):
                return None
            cur = cur.get(part)
    return cur


class Builder:
    def __init__(self, orgnr: str):
        self.orgnr = orgnr
        self.claims: dict[str, dict] = {}
        self.evidence: dict[str, dict] = {}
        self.sources: dict[str, dict] = {}
        self.abstentions: list[dict] = []

    # ---- evidence -------------------------------------------------------------------------
    def _source(self, resp: Response, source_class: str) -> str:
        """Shared capture metadata (one per fetched response) referenced by evidence items."""
        sid = "src-" + resp.sha256[:12] + "-" + hashlib.sha1(resp.final_url.encode()).hexdigest()[:6]
        if sid not in self.sources:
            name, licence, access = SOURCES[source_class]
            src = {"fetched_url": resp.final_url, "retrieved_at": resp.retrieved_at, "content_sha256": resp.sha256,
                   "snapshot": resp.snapshot, "http_status": resp.status, "source_name": name, "licence": licence,
                   "access_policy": access, "parser_version": PARSER_VERSION}
            if resp.url != resp.final_url:
                src["requested_url"] = resp.url
            if resp.redirects:
                src["redirect_chain"] = resp.redirects + [resp.final_url]
            self.sources[sid] = src
        return sid

    def _ev(self, resp: Response, source_class: str, locator: dict, claim_span: str, method: str,
            reporting_period: dict | None = None, source_url: str | None = None) -> str:
        url = source_url or resp.final_url
        key = f"{url}|{resp.sha256}|{canon(locator)}|{claim_span}"
        eid = "ev-" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
        if eid not in self.evidence:
            ev = {"id": eid, "source_url": url, "source_class": source_class, "retrieved_at": resp.retrieved_at,
                  "content_sha256": resp.sha256, "claim_span": claim_span, "locator": locator,
                  "extraction_method": method, "source_ref": self._source(resp, source_class)}
            if reporting_period:
                ev["reporting_period"] = reporting_period
            self.evidence[eid] = ev
        return eid

    def ev_json(self, resp: Response, path: str, source_class: str = "official_registry",
                reporting_period: dict | None = None, source_url: str | None = None) -> str:
        value = jpath_get(resp.json(), path)
        return self._ev(resp, source_class, {"type": "json_path", "path": path}, canon(value)[:2000],
                        "json field copy", reporting_period, source_url)

    def ev_text(self, resp: Response, claim_span: str, method: str, source_class: str = "company_owned") -> str:
        return self._ev(resp, source_class, {"type": "text_span"}, claim_span[:600], method)

    # ---- claims ---------------------------------------------------------------------------
    def put(self, field: str, value, evidence_ids, confidence: float = 0.99, availability: str = "available",
            reporting_period: dict | None = None, note: str | None = None, derived_from: list | None = None) -> None:
        family, label = FIELDS[field]
        c = {"field": field, "family": family, "section": SECTION_OF_FAMILY[family], "label": label,
             "value": value, "availability": availability, "confidence": round(confidence, 3),
             "evidence_ids": list(dict.fromkeys(evidence_ids))}
        if reporting_period:
            c["reporting_period"] = reporting_period
        if note:
            c["note"] = note
        if derived_from:
            c["derived_from"] = derived_from
        self.claims[field] = c

    def absent(self, field: str, availability: str, reason: str, checked_sources: list | None = None,
               evidence_ids=(), value=None) -> None:
        assert availability in STATES and availability != "available"
        if field in self.claims and self.claims[field]["availability"] == "available":
            return
        family, label = FIELDS[field]
        c = {"field": field, "family": family, "section": SECTION_OF_FAMILY[family], "label": label,
             "value": value, "availability": availability, "confidence": None,
             "evidence_ids": list(dict.fromkeys(evidence_ids)), "reason": reason}
        if checked_sources:
            c["checked_sources"] = checked_sources
        self.claims[field] = c
        if availability == "ambiguous":
            self.abstentions.append({"field": field, "reason": reason})

    def has(self, field: str) -> bool:
        return self.claims.get(field, {}).get("availability") == "available"
