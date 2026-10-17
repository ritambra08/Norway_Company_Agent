from __future__ import annotations

from . import __version__, orgnr as orgnr_mod
from .collect import collect
from .config import Config
from .explain import summarize
from .extract import extract
from .http import BudgetExceeded, Http, HttpError, Snapshot, utcnow_iso
from .models import Profile
from .store import FAILED, Store, apply_update

AGENT = {"name": "norway-company-agent", "version": __version__, "model": None,
         "note": "deterministic pipeline; no LLM or paid API is called"}


def build_profile(http: Http, cfg: Config, store: Store, raw_input: str) -> dict:
    """Return the final, update-merged profile dict for one company (and persist it)."""
    now = utcnow_iso()
    norm = orgnr_mod.normalize(raw_input)

    if norm is None or not orgnr_mod.is_valid(norm):
        label = norm or str(raw_input)
        p = Profile(label, "invalid_orgnr", None, now, agent=AGENT)
        p.summary = summarize(label, p.status, None, [], [])
        d = p.to_dict()
        store.save_profile(d)
        return d

    snaps: dict[str, Snapshot] = {}
    error: str | None = None
    try:
        collect(http, cfg, norm, snaps)
    except (BudgetExceeded, HttpError) as e:
        error = str(e)

    ext = extract(norm, snaps)
    prev = store.load_profile(norm)
    # A failed refresh must not overwrite the saved evidence behind good stored facts.
    if ext.status not in FAILED or prev is None:
        store.save_raw(norm, snaps)

    status = ext.status
    notes = list(ext.notes)
    if error:
        notes.append(f"Run interrupted: {error}.")
        out_of_budget = "budget" in error or "cap" in error
        if status == "ok":
            status = "partial"  # core record fetched, some optional lookups did not run
        elif status in ("error", "not_found"):
            status = "skipped_budget" if out_of_budget else "error"

    p = Profile(norm, status, ext.entity_type, now, facts=ext.facts, not_found=ext.missing,
                notes=notes, agent=AGENT, last_error=error)
    p.summary = summarize(norm, status, ext.entity_type, ext.facts, notes)

    merged = apply_update(p.to_dict(), prev, now)
    store.save_profile(merged)
    return merged
