"""Fetch step: decide which Brønnøysund endpoints to call for one org number.

Network only. Nothing here interprets the data; ``extract.py`` does that on the
saved snapshots, which is what makes every profile replayable and auditable.
"""

from __future__ import annotations

from urllib.parse import urlencode

from .config import NO_ACCOUNTS_FORMS, Config
from .http import Http, Scope, Snapshot


def collect(http: Http, cfg: Config, orgnr: str, snaps: dict[str, Snapshot]) -> None:
    """Fill ``snaps`` in place so a budget error part-way keeps what we already have."""
    scope = Scope(cfg.max_requests_per_company)
    base = cfg.base_url

    enhet = snaps["enhet"] = http.get_json(f"{base}/enhetsregisteret/api/enheter/{orgnr}", scope)

    if enhet.status == 404:
        # Not a main unit. It may be an establishment (underenhet), which has its
        # own org number and must never be merged with its parent.
        snaps["underenhet"] = http.get_json(
            f"{base}/enhetsregisteret/api/underenheter/{orgnr}", scope
        )
        return
    if enhet.status != 200:
        return

    body = enhet.body
    if not isinstance(body, dict) or body.get("organisasjonsnummer") != orgnr:
        return  # identity mismatch: extract() will reject it; fetch nothing further

    if cfg.include_roles:
        snaps["roller"] = http.get_json(
            f"{base}/enhetsregisteret/api/enheter/{orgnr}/roller", scope
        )
    if cfg.include_subunits:
        q = urlencode({"overordnetEnhet": orgnr, "size": 100})
        snaps["underenheter_list"] = http.get_json(
            f"{base}/enhetsregisteret/api/underenheter?{q}", scope
        )
    form = ((body.get("organisasjonsform") or {}).get("kode")) or ""
    if cfg.include_financials and form not in NO_ACCOUNTS_FORMS:
        snaps["regnskap"] = http.get_json(f"{base}/regnskapsregisteret/regnskap/{orgnr}", scope)
