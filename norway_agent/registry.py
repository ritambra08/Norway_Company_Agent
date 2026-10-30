"""Brønnøysund collection: identity (batched), roles, establishments, accounts, change events.

Network only. Interpretation happens in ``extract.py`` on the stored responses.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from .config import NO_ACCOUNTS_FORMS, Config
from .http import BudgetExceeded, Http
from .snapshots import SnapshotStore
from .state import CompanyState, Fetch, SourceStatus

BATCH = 100


def _chunks(xs: list, n: int):
    for i in range(0, len(xs), n):
        yield xs[i:i + n]


def _budget_error(s: CompanyState, key: str, e: BudgetExceeded) -> None:
    s.status[key] = SourceStatus("not_checked", f"not checked: {e}")


def fetch_identity(http: Http, cfg: Config, snaps: SnapshotStore, states: list[CompanyState]) -> None:
    by_org = {s.orgnr: s for s in states if s.valid}
    base = f"{cfg.brreg_base}/enhetsregisteret/api"

    def batch(kind: str, orgs: list[str]) -> set[str]:
        got: set[str] = set()
        for chunk in _chunks(orgs, BATCH):
            q = urlencode({"organisasjonsnummer": ",".join(chunk), "size": BATCH}, safe=",")
            try:
                r = http.get(f"{base}/{kind}?{q}", lane="brreg")
            except BudgetExceeded:
                return got
            snaps.put(r)
            body = r.json() if r.ok else None
            if not isinstance(body, dict):
                continue  # these numbers fall through to single lookups below
            items = (body.get("_embedded") or {}).get(kind) or []
            for i, e in enumerate(items):
                o = e.get("organisasjonsnummer") if isinstance(e, dict) else None
                if o in by_org and o in chunk and o not in got:  # exact identity match only
                    s = by_org[o]
                    s.kind = "enhet" if kind == "enheter" else "underenhet"
                    s.record = Fetch(r, f"_embedded.{kind}[{i}]", e)
                    s.identity = SourceStatus("checked")
                    got.add(o)
        return got

    orgs = list(by_org)
    found = batch("enheter", orgs)
    rest = [o for o in orgs if o not in found]
    if rest:
        found |= batch("underenheter", rest)
    rest = [o for o in orgs if o not in found]

    # Single lookups distinguish "deleted" (410) from "never existed" (404) and recover failed batches.
    def single(o: str) -> None:
        s = by_org[o]
        try:
            for kind in ("enheter", "underenheter"):
                r = http.get(f"{base}/{kind}/{o}", lane="brreg", scope=s.scope)
                snaps.put(r)
                body = r.json()
                if r.status == 200 and isinstance(body, dict):
                    if body.get("organisasjonsnummer") != o:
                        s.identity = SourceStatus("ambiguous", f"record returned for {o} carries "
                                                               f"{body.get('organisasjonsnummer')}")
                        return
                    s.kind = "enhet" if kind == "enheter" else "underenhet"
                    s.record = Fetch(r, "", body)
                    s.identity = SourceStatus("checked")
                    return
                if r.status == 410:
                    s.kind = "enhet" if kind == "enheter" else "underenhet"
                    s.record = Fetch(r, "", body if isinstance(body, dict) else {})
                    s.identity = SourceStatus("deleted", f"HTTP 410 from {kind}")
                    return
                if r.status != 404:
                    s.identity = SourceStatus("failed", f"HTTP {r.status} {r.error or ''}".strip())
                    return
            s.identity = SourceStatus("not_found", "not registered as a main unit or an establishment")
        except BudgetExceeded as e:
            s.identity = SourceStatus("failed", f"not checked: {e}")

    with ThreadPoolExecutor(max_workers=cfg.api_workers) as ex:
        list(ex.map(single, rest))


def fetch_company_records(http: Http, cfg: Config, snaps: SnapshotStore, states: list[CompanyState]) -> None:
    """Accounts, roles and establishments, breadth-first by source so a budget stop degrades
    one source for the tail of the batch rather than dropping whole companies."""
    base = cfg.brreg_base
    live = [s for s in states if s.kind == "enhet" and s.identity.state == "checked"]

    def accounts(s: CompanyState) -> None:
        form = ((s.body().get("organisasjonsform") or {}).get("kode")) or ""
        if form in NO_ACCOUNTS_FORMS and not s.body().get("sisteInnsendteAarsregnskap"):
            s.status["accounts"] = SourceStatus("not_applicable", f"organisation form {form} does not file "
                                                                  "annual accounts with Regnskapsregisteret")
            return
        try:
            r = http.get(f"{base}/regnskapsregisteret/regnskap/{s.orgnr}", lane="brreg", scope=s.scope)
        except BudgetExceeded as e:
            _budget_error(s, "accounts", e)
            return
        snaps.put(r)
        s.accounts = Fetch(r, "", r.json())
        s.status["accounts"] = SourceStatus("checked" if r.ok else ("not_found" if r.status == 404 else "failed"),
                                            f"HTTP {r.status}")

    def roles(s: CompanyState) -> None:
        try:
            r = http.get(f"{base}/enhetsregisteret/api/enheter/{s.orgnr}/roller", lane="brreg", scope=s.scope)
        except BudgetExceeded as e:
            _budget_error(s, "roles", e)
            return
        snaps.put(r)
        s.roles = Fetch(r, "", r.json())
        s.status["roles"] = SourceStatus("checked" if r.ok else ("not_found" if r.status == 404 else "failed"),
                                         f"HTTP {r.status}")

    def subunits(s: CompanyState) -> None:
        q = urlencode({"overordnetEnhet": s.orgnr, "size": 200})
        try:
            r = http.get(f"{base}/enhetsregisteret/api/underenheter?{q}", lane="brreg", scope=s.scope)
        except BudgetExceeded as e:
            _budget_error(s, "subunits", e)
            return
        snaps.put(r)
        s.subunits = Fetch(r, "_embedded.underenheter", r.json())
        s.status["subunits"] = SourceStatus("checked" if r.ok else "failed", f"HTTP {r.status}")

    if not cfg.enable_registry_extras:
        return
    with ThreadPoolExecutor(max_workers=cfg.api_workers) as ex:
        for fn in (accounts, roles, subunits):  # priority order
            list(ex.map(fn, live))


def fetch_change_events(http: Http, cfg: Config, snaps: SnapshotStore, states: list[CompanyState]) -> None:
    """Dated official change events (entity updates and role updates), batched 100 per request."""
    if not cfg.enable_activity:
        return
    live = {s.orgnr: s for s in states if s.kind == "enhet" and s.identity.state == "checked"}
    if not live:
        return
    since = datetime.now(timezone.utc) - timedelta(days=cfg.activity_window_days)
    stamp = since.strftime("%Y-%m-%dT00:00:00.000Z")
    base = f"{cfg.brreg_base}/enhetsregisteret/api/oppdateringer"
    for chunk in _chunks(list(live), BATCH):
        ok_e = ok_r = False
        page = 0
        while True:
            q = urlencode({"organisasjonsnummer": ",".join(chunk), "dato": stamp, "includeChanges": "true",
                           "size": 10000, "page": page}, safe=",:")
            try:
                r = http.get(f"{base}/enheter?{q}", lane="brreg")
            except BudgetExceeded:
                break
            snaps.put(r)
            body = r.json() if r.ok else None
            if not isinstance(body, dict):
                break
            ok_e = True
            for i, ev in enumerate((body.get("_embedded") or {}).get("oppdaterteEnheter") or []):
                o = ev.get("organisasjonsnummer") if isinstance(ev, dict) else None
                if o in live and o in chunk:
                    live[o].updates.append(Fetch(r, f"_embedded.oppdaterteEnheter[{i}]", ev))
            pg = body.get("page") or {}
            if page + 1 >= int(pg.get("totalPages") or 1):
                break
            page += 1
        q = urlencode({"organisasjonsnummer": ",".join(chunk), "afterTime": stamp, "size": 10000}, safe=",:")
        try:
            r = http.get(f"{base}/roller?{q}", lane="brreg")
            snaps.put(r)
            body = r.json() if r.ok else None
            if isinstance(body, list):
                ok_r = True
                for i, ev in enumerate(body):
                    o = ((ev or {}).get("data") or {}).get("organisasjonsnummer")
                    if o in live and o in chunk:
                        live[o].role_events.append(Fetch(r, f"[{i}]", ev))
        except BudgetExceeded:
            pass
        for o in chunk:
            live[o].status["entity_events"] = SourceStatus("checked" if ok_e else "failed")
            live[o].status["role_events"] = SourceStatus("checked" if ok_r else "failed")


def fetch_parents_of(http: Http, cfg: Config, snaps: SnapshotStore, orgnrs: list[str]) -> dict[str, tuple]:
    """Resolve establishment org numbers to their parent unit (used to tie job ads posted by an
    establishment back to its legal entity without merging the two)."""
    out: dict[str, tuple] = {}
    base = f"{cfg.brreg_base}/enhetsregisteret/api/underenheter"
    for chunk in _chunks(sorted(set(orgnrs)), BATCH):
        q = urlencode({"organisasjonsnummer": ",".join(chunk), "size": BATCH}, safe=",")
        try:
            r = http.get(f"{base}?{q}", lane="brreg")
        except BudgetExceeded:
            break
        snaps.put(r)
        body = r.json() if r.ok else None
        for i, u in enumerate(((body or {}).get("_embedded") or {}).get("underenheter") or []):
            if isinstance(u, dict) and u.get("organisasjonsnummer") in chunk:
                out[u["organisasjonsnummer"]] = (u.get("overordnetEnhet"), Fetch(r, f"_embedded.underenheter[{i}]", u))
    return out
