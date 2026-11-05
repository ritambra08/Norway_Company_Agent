"""Job postings from NAV's public job-vacancy feed (arbeidsplassen.nav.no), tied by org number.

How it stays exact:
  1. Scan the feed headers (title, employer name, status) for the recent window.
  2. Candidate = an ACTIVE ad whose employer name equals the company's registered name, a former
     name or one of its registered establishments' names (after folding legal-form words).
  3. Publish only if the ad's own ``employer.orgnr`` is the company itself, or one of its
     registered establishments (checked against the register). Anything else is rejected.

Contact persons in ads are never copied. Inactive ads are dropped, per NAV's terms of use.
"""

from __future__ import annotations

import os
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

from .config import Config
from .http import BudgetExceeded, Http
from .names import GENERIC, match_key
from .registry import fetch_parents_of
from .snapshots import SnapshotStore
from .state import CompanyState, Fetch, SourceStatus

JWT = re.compile(r"[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")
AD_URL = "https://arbeidsplassen.nav.no/stillinger/stilling/{uuid}"


def _token(http: Http, cfg: Config) -> tuple[str | None, str]:
    env = os.environ.get("NAV_FEED_TOKEN", "").strip()
    if env:
        return env.removeprefix("Bearer ").strip(), "NAV_FEED_TOKEN (registered consumer token)"
    try:
        r = http.get(f"{cfg.nav_base}/api/publicToken", lane="nav", headers={"Accept": "text/plain"})
    except BudgetExceeded:
        return None, "budget"
    m = JWT.search(r.text()) if r.ok else None
    return (m.group(0), "NAV public token (published at /api/publicToken)") if m else (None, f"HTTP {r.status}")


def _names_for(s: CompanyState) -> set[str]:
    names = {s.name()}
    for h in (s.body().get("historiskeNavn") or [])[-3:]:
        if isinstance(h, dict) and h.get("navn"):
            names.add(h["navn"])
    for u in s.subunit_list():
        if u.get("navn"):
            names.add(u["navn"])
    keys = {match_key(n) for n in names if n}
    return {k for k in keys if len(k.replace(" ", "")) >= 3}


def collect_jobs(http: Http, cfg: Config, snaps: SnapshotStore, states: list[CompanyState]) -> dict:
    meta: dict = {"source": "NAV job vacancy feed", "pages": 0, "entries": 0, "active_ads": 0,
                  "complete": False, "window_days": cfg.jobs_window_days, "token": None, "error": None}
    live = [s for s in states if s.kind == "enhet" and s.identity.state == "checked"]
    if not cfg.enable_jobs or not live:
        for s in live:
            s.status["nav_jobs"] = SourceStatus("not_checked", "jobs source disabled")
        return meta

    def mark(state: str, detail: str):
        for s in live:
            s.status.setdefault("nav_jobs", SourceStatus(state, detail))

    token, how = _token(http, cfg)
    meta["token"] = how
    if not token:
        meta["error"] = f"no feed token ({how})"
        mark("failed", meta["error"])
        return meta
    auth = {"Authorization": f"Bearer {token}"}
    since = datetime.now(timezone.utc) - timedelta(days=cfg.jobs_window_days)
    url = f"{cfg.nav_base}/api/v1/feed?pageSize={cfg.jobs_page_size}"
    headers = dict(auth, **{"If-Modified-Since": format_datetime(since, usegmt=True)})
    latest: dict[str, dict] = {}
    t_end = http.elapsed() + cfg.jobs_time_budget_s
    page_cap = max(1, min(cfg.jobs_max_pages, http.remaining() // 4))
    while url and meta["pages"] < page_cap and http.elapsed() < t_end:
        try:
            r = http.get(url, lane="nav", headers=headers)
        except BudgetExceeded as e:
            meta["error"] = str(e)
            break
        headers = auth
        if r.status == 304:
            meta["complete"] = True
            break
        body = r.json() if r.ok else None
        if not isinstance(body, dict):
            meta["error"] = f"feed page HTTP {r.status} {r.error or ''}".strip()
            break
        snap = snaps.put(r)
        meta["pages"] += 1
        for i, it in enumerate(body.get("items") or []):
            fe = (it or {}).get("_feed_entry") or {}
            uid = fe.get("uuid")
            if not uid:
                continue
            meta["entries"] += 1
            latest[uid] = {"status": fe.get("status"), "businessName": fe.get("businessName") or "",
                           "title": fe.get("title") or it.get("title"), "municipal": fe.get("municipal"),
                           "sistEndret": fe.get("sistEndret"), "entry_url": it.get("url"),
                           "page_snapshot": snap, "page_url": r.final_url, "index": i}
        nxt = body.get("next_url")
        if not nxt or not body.get("next_id"):
            meta["complete"] = True
            url = None
        else:
            url = f"{cfg.nav_base}{nxt}" if nxt.startswith("/") else nxt
            if "pageSize=" not in url:
                url += ("&" if "?" in url else "?") + f"pageSize={cfg.jobs_page_size}"

    active = {u: v for u, v in latest.items() if v["status"] == "ACTIVE"}
    meta["active_ads"] = len(active)
    by_name: dict[str, list[str]] = defaultdict(list)
    by_token: dict[str, set[str]] = defaultdict(set)
    ad_tokens: dict[str, frozenset] = {}
    for uid, v in active.items():
        k = match_key(v["businessName"])
        if k:
            by_name[k].append(uid)
            toks = frozenset(k.split())
            ad_tokens[uid] = toks
            for t in toks:
                by_token[t].add(uid)

    # Candidates only (never evidence): exact name, then the ad name containing every word of the
    # company's name ("Sagen Bygg Stavanger"), then a short brand made of the company's words ("Sagen").
    # Publication still requires the ad's own employer org number to match (below).
    cands: dict[str, list[str]] = {}
    for s in live:
        exact, sup, sub = [], [], []
        for k in _names_for(s):
            exact.extend(by_name.get(k, []))
            toks = k.split()
            distinct = [t for t in toks if t not in GENERIC and len(t) >= 4 and not t.isdigit()]
            if not distinct:
                continue
            pool = set.intersection(*(by_token.get(t, set()) for t in toks)) if toks else set()
            sup.extend(sorted(pool))
            tokset = set(toks)
            for t in distinct:
                for uid in by_token.get(t, ()):
                    at = ad_tokens[uid]
                    if at <= tokset and any(x not in GENERIC and len(x) >= 4 for x in at):
                        sub.append(uid)
        ids = list(dict.fromkeys(exact + sup + sub))
        if ids:
            cands[s.orgnr] = ids[: cfg.jobs_max_details_per_company]
    meta["candidate_ads"] = sum(len(v) for v in cands.values())

    states_by = {s.orgnr: s for s in live}
    details: dict[str, list] = defaultdict(list)

    def detail(job: tuple[str, str]) -> None:
        org, uid = job
        s = states_by[org]
        entry = active[uid]
        u = entry["entry_url"] or f"/api/v1/feedentry/{uid}"
        full = f"{cfg.nav_base}{u}" if u.startswith("/") else u
        try:
            r = http.get(full, lane="nav", headers=auth, scope=s.scope)
        except BudgetExceeded:
            return
        snaps.put(r)
        body = r.json() if r.ok else None
        ad = (body or {}).get("ad_content") or (body or {}).get("json") if isinstance(body, dict) else None
        if isinstance(ad, dict):
            details[org].append((r, ad, (body or {}).get("status")))

    jobs = [(o, uid) for o, ids in cands.items() for uid in ids]
    with ThreadPoolExecutor(max_workers=4) as ex:
        list(ex.map(detail, jobs))

    # Resolve employer org numbers that are neither the company nor a listed establishment.
    unknown = []
    for org, rows in details.items():
        known = {org} | {u.get("organisasjonsnummer") for u in states_by[org].subunit_list()}
        for _, ad, _ in rows:
            e = ((ad.get("employer") or {}).get("orgnr") or "").replace(" ", "")
            if e and e not in known:
                unknown.append(e)
    parents = fetch_parents_of(http, cfg, snaps, unknown) if unknown else {}

    now = datetime.now(timezone.utc).isoformat()
    meta["details_fetched"] = sum(len(v) for v in details.values())
    for s in live:
        if s.orgnr in cands and s.orgnr in details:
            subs = {u.get("organisasjonsnummer") for u in s.subunit_list()}
            for r, ad, status in details[s.orgnr]:
                e = ((ad.get("employer") or {}).get("orgnr") or "").replace(" ", "")
                if status and status != "ACTIVE":
                    continue
                if (ad.get("expires") or "9999") < now[:10]:
                    continue
                if e == s.orgnr:
                    relation = "self"
                elif e in subs or (e in parents and parents[e][0] == s.orgnr):
                    relation = "establishment"
                else:
                    s.nav_rejected += 1  # same name, different legal entity: never published
                    continue
                s.nav_ads.append(Fetch(r, relation, ad))
        meta["rejected_other_orgnr"] = meta.get("rejected_other_orgnr", 0)
        meta["rejected_other_orgnr"] += s.nav_rejected
        if meta["pages"] == 0:
            s.status["nav_jobs"] = SourceStatus("failed", meta["error"] or "feed not read")
        else:
            s.status["nav_jobs"] = SourceStatus("checked", "complete scan" if meta["complete"] else
                                                f"partial scan ({meta['pages']} pages)")
    return meta
