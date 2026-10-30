"""Refresh: compare with the previous envelope, emit typed material changes, keep good evidence.

* Same snapshot in, same claims out: values never contain retrieval times, so a rerun on unchanged
  sources yields zero changes (idempotent).
* A source that failed this time never erases a previously supported value: the previous claim is
  carried forward with its original evidence and marked ``stale``.
* Every change records before/after values and the evidence ids on both sides; the previous
  evidence objects are kept in the envelope.
"""

from __future__ import annotations

import copy

from .claims import canon

MATERIAL = {"legal_name", "registry_status", "business_address", "leadership", "chief_executive", "board_chair",
            "annual_accounts_latest", "official_website", "job_postings", "registered_workplaces",
            "social_profiles", "organisation_form", "registered_employees", "industry", "share_capital",
            "auditor", "hiring_signal"}
TRANSIENT = {"failed"}


def _key_role(r: dict) -> tuple:
    return (r.get("role_code"), r.get("name"), r.get("holder_orgnr"))


def _diff_list(field: str, before: list, after: list, key, add: str, remove: str) -> list[dict]:
    b = {key(x): x for x in before or [] if isinstance(x, dict)}
    a = {key(x): x for x in after or [] if isinstance(x, dict)}
    out = []
    for k in sorted(set(a) - set(b), key=str):
        out.append({"type": add, "field": field, "before": None, "after": a[k]})
    for k in sorted(set(b) - set(a), key=str):
        out.append({"type": remove, "field": field, "before": b[k], "after": None})
    return out


def typed_changes(field: str, before, after) -> list[dict]:
    if field == "leadership":
        return _diff_list(field, before, after, _key_role, "new_role", "removed_role")
    if field == "job_postings":
        k = lambda p: p.get("url") or p.get("title")  # noqa: E731
        return _diff_list(field, before, after, k, "new_job", "closed_job")
    if field == "registered_workplaces":
        return _diff_list(field, (before or {}).get("items"), (after or {}).get("items"),
                          lambda u: u.get("orgnr"), "new_location", "closed_location") or \
            [{"type": "changed_registered_workplaces", "field": field, "before": before, "after": after}]
    if field == "social_profiles":
        return _diff_list(field, before, after, lambda p: p.get("url"), "new_profile", "removed_profile")
    if field == "public_activity":
        return _diff_list(field, before, after, lambda x: (x.get("date"), x.get("kind"), x.get("title")),
                          "new_activity", "dropped_activity")
    if field == "annual_accounts_latest":
        if (before or {}).get("period_end") != (after or {}).get("period_end"):
            return [{"type": "new_filing", "field": field, "before": before, "after": after}]
        return [{"type": "restated_accounts", "field": field, "before": before, "after": after}]
    named = {"legal_name": "changed_name", "business_address": "changed_address", "registry_status": "changed_status",
             "official_website": "changed_website", "website_description": "changed_description",
             "registered_employees": "changed_employee_count", "chief_executive": "changed_chief_executive",
             "board_chair": "changed_board_chair"}
    return [{"type": named.get(field, f"changed_{field}"), "field": field, "before": before, "after": after}]


def apply(env: dict, prev: dict | None, now: str) -> dict:
    """Mutates and returns ``env`` with changes, carry-forward and first-seen/last-verified stamps."""
    env.setdefault("changes", [])
    refresh = {"previous_run_id": None, "previous_completed_at": None, "carried_forward": [],
               "mode": "first_run"}
    if not prev:
        for c in env["claims"]:
            if c["availability"] == "available":
                c["first_seen_at"] = now
                c["last_verified_at"] = now
        env["refresh"] = refresh
        return env
    prun = prev.get("run") or {}
    refresh.update(previous_run_id=prun.get("run_id"), previous_completed_at=prun.get("completed_at"), mode="refresh")
    pclaims = {c["field"]: c for c in prev.get("claims") or []}
    pev = {e["id"]: e for e in prev.get("evidence") or []}
    ev_ids = {e["id"] for e in env["evidence"]}
    keep_prev_ev: set[str] = set()
    out_claims = []
    for c in env["claims"]:
        p = pclaims.get(c["field"])
        if p and p.get("availability") == "available" and c["availability"] in TRANSIENT:
            cf = copy.deepcopy(p)
            cf["stale"] = True
            cf["carried_forward_from"] = prun.get("run_id")
            cf["refresh_failure"] = c.get("reason")
            out_claims.append(cf)
            refresh["carried_forward"].append(c["field"])
            keep_prev_ev.update(p.get("evidence_ids") or [])
            continue
        if c["availability"] == "available":
            c["last_verified_at"] = now
            if p and p.get("availability") == "available" and canon(p.get("value")) == canon(c.get("value")):
                c["first_seen_at"] = p.get("first_seen_at") or prun.get("completed_at") or now
            else:
                c["first_seen_at"] = now
        if p:
            pa, ca = p.get("availability"), c["availability"]
            if pa == "available" and ca == "available" and canon(p.get("value")) != canon(c.get("value")):
                for ch in typed_changes(c["field"], p.get("value"), c.get("value")):
                    ch.update(detected_at=now, material=c["field"] in MATERIAL,
                              evidence_ids=c.get("evidence_ids"), previous_evidence_ids=p.get("evidence_ids"))
                    env["changes"].append(ch)
                keep_prev_ev.update(p.get("evidence_ids") or [])
            elif pa != ca and not (ca in TRANSIENT):
                env["changes"].append({"type": "availability_changed", "field": c["field"], "before": pa, "after": ca,
                                       "before_value": p.get("value"), "after_value": c.get("value"),
                                       "detected_at": now, "material": c["field"] in MATERIAL and pa == "available",
                                       "evidence_ids": c.get("evidence_ids"),
                                       "previous_evidence_ids": p.get("evidence_ids"), "reason": c.get("reason")})
                if pa == "available":
                    keep_prev_ev.update(p.get("evidence_ids") or [])
        out_claims.append(c)
    env["claims"] = out_claims
    psrc = prev.get("sources") or {}
    env.setdefault("sources", {})
    for eid in sorted(keep_prev_ev):
        if eid not in ev_ids and eid in pev:
            e = copy.deepcopy(pev[eid])
            e["from_previous_run"] = prun.get("run_id")
            env["evidence"].append(e)
            ev_ids.add(eid)
            ref = e.get("source_ref")
            if ref and ref not in env["sources"] and ref in psrc:
                env["sources"][ref] = psrc[ref]
    env["refresh"] = refresh
    return env
