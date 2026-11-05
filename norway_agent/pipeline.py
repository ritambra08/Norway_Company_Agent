"""The run: read batch -> collect breadth-first by source -> extract -> envelopes, report, viewer."""

from __future__ import annotations

import json
import shutil
import sys
import time
import traceback
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from . import __version__, jsonl, orgnr as orgnr_mod
from .claims import FIELDS, Builder
from .config import Config
from .envelope import AGENT, assemble
from .extract import build
from .http import Http, Scope, utcnow_iso
from .inputs import read_batch
from .nav import collect_jobs
from .refresh import apply as apply_refresh
from .registry import fetch_change_events, fetch_company_records, fetch_identity
from .snapshots import SnapshotStore
from .state import CompanyState, SourceStatus
from .summary import build as build_summary
from .viewer import write_cards, write_viewer
from .web import crawl_websites


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def _load_previous(path: Path | None) -> dict[str, dict]:
    if not path or not Path(path).exists():
        return {}
    out = {}
    for line in jsonl.lines(Path(path).read_text(encoding="utf-8")):
        try:
            e = json.loads(line)
            out[e["organisation_number"]] = e
        except (ValueError, KeyError):
            log(f"previous envelopes: skipped an unreadable line in {path}")
            continue
    return out


def _fallback_envelope(s: CompanyState, run: dict, err: str) -> dict:
    return {"schema_version": "signalpost-envelope/1.0", "organisation_number": s.orgnr or s.input, "status": "failed",
            "legal_identity": None, "run": {"run_id": run["run_id"], "started_at": run["started_at"],
                                            "completed_at": None, "terminal_status": "failed", "agent": AGENT},
            "summary": None, "availability": {}, "claims": [], "evidence": [], "sources": {}, "changes": [],
            "errors": [{"source": "agent", "message": err}], "abstentions": [], "refresh": None,
            "operations": {"requests": s.scope.used, "runtime_ms": s.scope.elapsed_ms, "third_party_cost_usd": 0}}


def run(batch: str, output: Path, report_path: Path, run_id: str, cfg: Config, previous: Path | None = None,
        profiles_output: Path | None = None, viewer_path: Path | None = None, expected_count: int | None = None,
        opener=None, clock=None, sleep=None) -> dict:
    t0 = time.monotonic()
    started = utcnow_iso()
    output = Path(output)
    out_dir = output.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    inputs, dups = read_batch(batch)
    cfg.resolve(len(inputs))
    kw = {}
    if clock:
        kw["clock"] = clock
    if sleep:
        kw["sleep"] = sleep
    http = Http(cfg, opener=opener, **kw)
    snaps = SnapshotStore(out_dir)
    prev = _load_previous(previous)
    run_meta = {"run_id": run_id, "started_at": started}
    per_company_cap = max(12, cfg.requests_per_company * 2)
    states: list[CompanyState] = []
    for x in inputs:
        valid = orgnr_mod.is_valid(x)
        states.append(CompanyState(input=x, orgnr=x if valid else None, valid=valid, scope=Scope(per_company_cap)))
    log(f"{len(states)} companies ({sum(s.valid for s in states)} valid), request budget {http.max_requests}, "
        f"deadline {cfg.deadline_seconds / 60:.0f} min")
    stage_errors: list[str] = []

    def stage(name: str, fn, *a):
        if not http.can_start():
            stage_errors.append(f"{name}: skipped (budget or deadline reached)")
            log(f"skip {name}")
            return None
        t = time.monotonic()
        try:
            res = fn(*a)
        except Exception as e:  # noqa: BLE001 - a stage failure degrades, never kills, the run
            stage_errors.append(f"{name}: {type(e).__name__}: {e}")
            log(f"stage {name} failed: {e}\n{traceback.format_exc()}")
            return None
        log(f"{name} done in {time.monotonic() - t:.1f}s; requests so far {http.requests}")
        return res

    stage("identity", fetch_identity, http, cfg, snaps, states)
    stage("change_events", fetch_change_events, http, cfg, snaps, states)
    stage("registry_records", fetch_company_records, http, cfg, snaps, states)
    nav_meta = stage("jobs_nav", collect_jobs, http, cfg, snaps, states) or {}
    web_stats = stage("websites", crawl_websites, http, cfg, snaps, states) or {}

    # Sources that never ran for a live company are explicit "not checked", not silent gaps.
    for s in states:
        if s.kind == "enhet" and s.identity.state == "checked":
            for key in ("accounts", "roles", "subunits", "entity_events", "role_events", "nav_jobs", "website"):
                s.status.setdefault(key, SourceStatus("not_checked", "stage did not run (budget/deadline)"))

    completed = utcnow_iso()
    envs: list[dict] = []
    for s in states:
        try:
            b, _ = build(s, cfg.activity_window_days) if s.valid else (Builder(s.input), [])
            env = assemble(s, b, run_meta)
            env["run"]["completed_at"] = completed
            apply_refresh(env, prev.get(env["organisation_number"]), completed)
            env["summary"] = build_summary(env) if env.get("claims") else {
                "text": ("Not a valid organisation number." if not s.valid else
                         f"No company profile: {s.identity.state} ({s.identity.detail}).").strip(),
                "unknowns": [], "method": "deterministic"}
        except Exception as e:  # noqa: BLE001
            env = _fallback_envelope(s, run_meta, f"{type(e).__name__}: {e}")
            env["run"]["completed_at"] = completed
            log(f"envelope for {s.input} failed: {traceback.format_exc()}")
        envs.append(env)

    if output.exists():
        shutil.copyfile(output, out_dir / "previous-envelopes.jsonl")  # keep the last run for audit
    tmp = output.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for env in envs:
            fh.write(jsonl.dumps(env) + "\n")
    tmp.replace(output)

    report = make_report(envs, states, inputs, dups, http, cfg, run_id, started, completed, nav_meta, web_stats,
                         stage_errors, expected_count, time.monotonic() - t0, batch, output)
    Path(report_path).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    cards = write_cards(envs, profiles_output or out_dir / "profiles.jsonl")
    if viewer_path is not False:
        write_viewer(cards, report, viewer_path or out_dir / "index.html")
    log(f"wrote {len(envs)} envelopes to {output}; requests {http.requests}; {report['runtime_seconds']}s")
    return report


def _pct(xs: list[int], q: float) -> int:
    if not xs:
        return 0
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))]


def make_report(envs, states, inputs, dups, http, cfg, run_id, started, completed, nav_meta, web_stats,
                stage_errors, expected_count, runtime, batch, output) -> dict:
    fam_cov: Counter = Counter()
    field_cov: Counter = Counter()
    state_cov: dict[str, Counter] = {}
    for e in envs:
        fams = set()
        for c in e.get("claims") or []:
            state_cov.setdefault(c["field"], Counter())[c["availability"]] += 1
            if c["availability"] == "available":
                field_cov[c["field"]] += 1
                fams.add(c["family"])
        for f in fams:
            fam_cov[f] += 1
    changes = Counter(ch["type"] for e in envs for ch in e.get("changes") or [])
    lat = [s.scope.elapsed_ms for s in states if s.valid]
    n = len(envs)
    return {
        "run_id": run_id, "agent": AGENT, "started_at": started, "completed_at": completed,
        "runtime_seconds": round(runtime, 1),
        "input": {"path": str(batch), "unique_inputs": len(inputs), "duplicates_dropped": len(dups),
                  "invalid_org_numbers": sum(1 for s in states if not s.valid)},
        "expected_count": expected_count,
        "envelopes_written": n,
        "one_terminal_envelope_per_input": n == len(inputs) and len({e["organisation_number"] for e in envs}) == n,
        "expected_count_met": None if expected_count is None else n == expected_count,
        "status_counts": dict(Counter(e["status"] for e in envs)),
        "terminal_status_counts": dict(Counter(e["run"]["terminal_status"] for e in envs)),
        "requests": {"total": http.requests, "budget": http.max_requests, "by_source": dict(http.by_lane),
                     "retried_errors": http.errors},
        "third_party_cost_usd": 0.0,
        "cost_note": "No paid API or model. Brønnøysund and NAV APIs are free; websites are fetched directly.",
        "per_company_latency_ms": {"p50": _pct(lat, 0.5), "p95": _pct(lat, 0.95)},
        "coverage_by_family": {f: {"companies": fam_cov[f], "share": round(fam_cov[f] / n, 3) if n else 0}
                               for f in sorted({v[0] for v in FIELDS.values()})},
        "coverage_by_field": {f: dict(state_cov.get(f, {})) for f in FIELDS},
        "changes": {"total": sum(changes.values()), "by_type": dict(changes),
                    "companies_with_material_changes": sum(1 for e in envs if any(c.get("material") for c in e.get("changes") or []))},
        "carried_forward_claims": sum(len((e.get("refresh") or {}).get("carried_forward") or []) for e in envs),
        "sources": {"nav_jobs": nav_meta, "websites": web_stats},
        "stage_errors": stage_errors,
        "limits": {"max_requests": http.max_requests, "deadline_seconds": cfg.deadline_seconds,
                   "web_pages_per_company": cfg.web_pages_per_company, "jobs_window_days": cfg.jobs_window_days,
                   "activity_window_days": cfg.activity_window_days},
        "outputs": {"envelopes": str(output), "snapshots": str(Path(output).parent / "snapshots")},
        "version": __version__,
    }
