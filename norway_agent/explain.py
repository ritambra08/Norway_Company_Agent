"""Plain-language summary built only from already-verified facts (no model, no guessing)."""

from __future__ import annotations

from .models import Fact


def _money(v: dict) -> str:
    amt = v["amount"]
    s = f"{amt:,.0f}".replace(",", " ")
    return f"{v['currency']} {s}"


def summarize(orgnr: str, status: str, entity_type: str | None, facts: list[Fact], notes: list[str]) -> str:
    by = {f.key: f.value for f in facts}
    if status == "invalid_orgnr":
        return f"{orgnr} is not a valid Norwegian organisation number (checksum or length), so nothing was looked up."
    if status == "not_found":
        return f"No entity with organisation number {orgnr} exists in Enhetsregisteret (neither as main unit nor establishment)."
    if status == "deleted":
        when = by.get("deletion_date")
        return f"{by.get('name', orgnr)} ({orgnr}) has been deleted from the register" + (f" on {when}." if when else ".")
    if status in ("error", "skipped_budget"):
        return f"{orgnr}: no profile could be built this run. {' '.join(notes)}".strip()

    parts = []
    name = by.get("name", orgnr)
    form = (by.get("organisation_form") or {}).get("description")
    kind = "establishment" if entity_type == "underenhet" else (form or "entity")
    head = f"{name} ({orgnr}) is a registered {kind}"
    city = (by.get("business_address") or {}).get("municipality")
    if city:
        head += f" in {city}"
    if by.get("registration_date"):
        head += f", registered in Enhetsregisteret on {by['registration_date']}"
    parts.append(head + ".")

    if by.get("operating_status") and by["operating_status"] != "active_in_register":
        parts.append(f"Register status: {by['operating_status'].replace('_', ' ')}.")
    nace = by.get("nace_primary")
    if nace:
        parts.append(f"Primary industry: {nace.get('description')} (NACE {nace.get('code')}).")
    if "employees" in by:
        parts.append(f"Registered employees: {by['employees']}.")
    if "revenue" in by:
        parts.append(f"Operating revenue for the period ending {by['revenue']['period_end']}: {_money(by['revenue'])}.")
    if "net_result" in by:
        parts.append(f"Net result: {_money(by['net_result'])}.")
    est = by.get("establishments")
    if est and est.get("total"):
        parts.append(f"{est['total']} registered establishment(s).")
    if by.get("parent_unit_orgnr"):
        parts.append(f"Belongs to main unit {by['parent_unit_orgnr']}.")
    if status == "partial":
        parts.append("This profile is partial because a run limit was hit before every lookup finished.")
    return " ".join(parts)
