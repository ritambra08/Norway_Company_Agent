"""Assemble one terminal envelope per input (the Signalpost output contract)."""

from __future__ import annotations

from . import __version__
from .claims import FIELDS, Builder, STATES
from .state import CompanyState

SCHEMA = "signalpost-envelope/1.0"
AGENT = {"name": "norway-company-agent", "version": __version__, "model": None,
         "note": "deterministic pipeline; no LLM and no paid API"}
ORDER = {f: i for i, f in enumerate(FIELDS)}
SECTIONS = ["company_brief", "latest_financials", "who_runs_it", "where_it_operates", "working_here",
            "recent_activity", "online_presence"]


def overall_status(s: CompanyState) -> tuple[str, str]:
    """(six-state status, run terminal_status)."""
    if not s.valid:
        return "not_available", "completed"
    st = s.identity.state
    if st in ("checked", "deleted"):
        problems = [k for k, v in s.status.items() if v.state in ("failed", "not_checked")]
        return "available", ("partial" if problems else "completed")
    if st == "not_found":
        return "not_available", "completed"
    if st == "ambiguous":
        return "ambiguous", "completed"
    if st == "blocked":
        return "blocked", "failed"
    return "failed", "failed"


def section_rollup(claims: list[dict]) -> dict:
    out = {}
    for sec in SECTIONS:
        states = [c["availability"] for c in claims if c.get("section") == sec]
        if not states:
            out[sec] = "not_applicable"
        elif "available" in states:
            out[sec] = "available"
        else:
            for st in ("ambiguous", "blocked", "failed", "not_available", "not_applicable"):
                if st in states:
                    out[sec] = st
                    break
    return out


def assemble(s: CompanyState, b: Builder, run: dict) -> dict:
    status, terminal = overall_status(s)
    claims = sorted(b.claims.values(), key=lambda c: ORDER.get(c["field"], 999))
    for c in claims:
        assert c["availability"] in STATES
    errors = list(s.errors)
    if not s.valid:
        errors.append({"source": "input", "message": f"'{s.input}' is not a valid Norwegian organisation number "
                                                     "(9 digits with a MOD-11 check digit)."})
    elif s.identity.state not in ("checked", "deleted"):
        errors.append({"source": "enhetsregisteret", "message": s.identity.detail or s.identity.state})
    for k, v in sorted(s.status.items()):
        if v.state in ("failed", "not_checked"):
            errors.append({"source": k, "message": f"{v.state}: {v.detail}".strip(": ")})
    body = s.body()
    ident = None
    if s.record is not None:
        ident = {"organisation_number": s.orgnr, "name": body.get("navn"),
                 "organisation_form": (body.get("organisasjonsform") or {}).get("kode"),
                 "entity_type": s.kind, "registry_status": (b.claims.get("registry_status") or {}).get("value"),
                 "match": "exact organisation number in Enhetsregisteret"}
        if s.kind == "underenhet":
            ident["parent_unit"] = body.get("overordnetEnhet")
    return {
        "schema_version": SCHEMA,
        "organisation_number": s.orgnr or s.input,
        "status": status,
        "legal_identity": ident,
        "run": {"run_id": run["run_id"], "started_at": run["started_at"], "completed_at": None,
                "terminal_status": terminal, "agent": AGENT},
        "summary": None,
        "availability": section_rollup(claims),
        "claims": claims,
        "evidence": list(b.evidence.values()),
        "sources": b.sources,
        "changes": [],
        "errors": errors,
        "abstentions": b.abstentions,
        "refresh": None,
        "operations": {"requests": s.scope.used, "runtime_ms": s.scope.elapsed_ms, "third_party_cost_usd": 0,
                       "note": "requests attributable to this company; batched registry/feed requests are "
                               "reported once in the run report"},
    }
