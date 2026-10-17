from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import __version__, orgnr as orgnr_mod
from .build import AGENT, build_profile
from .config import Config
from .http import Http, utcnow_iso
from .inputs import read_orgnrs
from .models import Profile
from .store import Store
from .validate import validate_dir


def cmd_run(a) -> int:
    cfg = Config(
        max_requests=a.max_requests,
        deadline_seconds=a.deadline_min * 60,
        workers=a.workers,
        requests_per_second=a.rps,
        include_roles=not a.no_roles,
        include_subunits=not a.no_subunits,
        include_financials=not a.no_financials,
    )
    orgs = [orgnr_mod.normalize(x) or x for x in a.orgnrs]
    if a.input:
        orgs += read_orgnrs(a.input)
    orgs = list(dict.fromkeys(orgs))[: a.limit or None]
    if not orgs:
        print("No organisation numbers given. Use --input FILE or pass numbers.", file=sys.stderr)
        return 2

    out = Path(a.out)
    store = Store(out)
    http = Http(cfg)
    started = utcnow_iso()

    def work(raw: str) -> dict:
        try:
            return build_profile(http, cfg, store, raw)
        except Exception as e:  # one bad company must never kill the run
            err = f"{type(e).__name__}: {e}"
            p = Profile(raw, "error", None, utcnow_iso(), summary=f"{raw}: internal error.",
                        last_error=err, agent=AGENT)
            return p.to_dict()

    counts: Counter = Counter()
    with ThreadPoolExecutor(max_workers=cfg.workers) as ex, open(out / "profiles.jsonl", "w", encoding="utf-8") as fh:
        for i, p in enumerate(ex.map(work, orgs), 1):
            counts[p["status"]] += 1
            fh.write(json.dumps(p, ensure_ascii=False) + "\n")
            if i % 10 == 0 or i == len(orgs):
                fh.flush()
                print(f"[{i}/{len(orgs)}] requests={http.requests} elapsed={http.elapsed():.0f}s {dict(counts)}",
                      file=sys.stderr)

    manifest = {
        "agent_version": __version__, "started_at": started, "finished_at": utcnow_iso(),
        "elapsed_seconds": round(http.elapsed(), 1), "outbound_requests": http.requests,
        "network_errors_retried": http.errors, "companies_requested": len(orgs),
        "by_status": dict(counts), "declared_external_api_cost_usd": cfg.declared_api_cost_usd,
        "llm": None,
        "limits": {"max_requests": cfg.max_requests, "deadline_seconds": cfg.deadline_seconds,
                   "max_requests_per_company": cfg.max_requests_per_company},
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2))
    return 0


def cmd_validate(a) -> int:
    n, problems = validate_dir(Path(a.out))
    for p in problems:
        print("PROBLEM:", p)
    print(f"{n} profiles checked, {len(problems)} problem(s).")
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    d = Config()
    ap = argparse.ArgumentParser(prog="norway_agent", description="Norwegian company profile agent")
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="build profiles for org numbers")
    r.add_argument("orgnrs", nargs="*", help="organisation numbers (spaces allowed)")
    r.add_argument("--input", "-i", help="file with org numbers (txt/csv/json/jsonl)")
    r.add_argument("--out", "-o", default="out")
    r.add_argument("--limit", type=int, default=0)
    r.add_argument("--workers", type=int, default=d.workers)
    r.add_argument("--rps", type=float, default=d.requests_per_second, help="max requests per second")
    r.add_argument("--max-requests", type=int, default=d.max_requests)
    r.add_argument("--deadline-min", type=float, default=d.deadline_seconds / 60)
    r.add_argument("--no-roles", action="store_true")
    r.add_argument("--no-subunits", action="store_true")
    r.add_argument("--no-financials", action="store_true")
    r.set_defaults(fn=cmd_run)

    v = sub.add_parser("validate", help="check evidence rules and replay raw snapshots")
    v.add_argument("--out", "-o", default="out")
    v.set_defaults(fn=cmd_validate)

    a = ap.parse_args(argv)
    return a.fn(a)
