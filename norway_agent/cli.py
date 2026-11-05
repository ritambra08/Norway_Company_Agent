from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import __version__, jsonl
from .config import Config
from .pipeline import run
from .validate import validate


def cmd_run(a) -> int:
    cfg = Config()
    if a.max_requests:
        cfg.max_requests = a.max_requests
    if a.deadline_min:
        cfg.deadline_seconds = a.deadline_min * 60
    for flag, attr in (("no_web", "enable_web"), ("no_jobs", "enable_jobs"), ("no_activity", "enable_activity"),
                       ("no_guess", "enable_domain_guess"), ("registry_only", None)):
        if getattr(a, flag, False):
            if attr:
                setattr(cfg, attr, False)
            else:
                cfg.enable_web = cfg.enable_jobs = False
    batch = a.organisations or a.input
    if not batch:
        print("Give the batch with --organisations FILE (JSONL/JSON/CSV/TXT of org numbers).", file=sys.stderr)
        return 2
    out_dir = Path(a.out) if a.out else None
    output = Path(a.output) if a.output else (out_dir or Path("out")) / "envelopes.jsonl"
    report = Path(a.report) if a.report else output.parent / "run-report.json"
    run_id = a.run_id or datetime.now(timezone.utc).strftime("run-%Y%m%dT%H%M%SZ")
    previous = Path(a.previous) if a.previous else (output if output.exists() else None)
    rep = run(batch, output, report, run_id, cfg, previous=previous,
              profiles_output=Path(a.profiles_output) if a.profiles_output else None,
              viewer_path=False if a.no_viewer else None, expected_count=a.expected_count)
    print(json.dumps({k: rep[k] for k in ("run_id", "envelopes_written", "one_terminal_envelope_per_input",
                                          "status_counts", "requests", "runtime_seconds", "coverage_by_family")},
                     indent=2, ensure_ascii=False))
    if a.expected_count is not None and rep["envelopes_written"] != a.expected_count:
        print(f"WARNING: expected {a.expected_count} envelopes, wrote {rep['envelopes_written']}", file=sys.stderr)
        return 1
    return 0


def cmd_validate(a) -> int:
    path = Path(a.output) if a.output else Path(a.out or "out") / "envelopes.jsonl"
    n, problems, stats = validate(path, a.organisations)
    for p in problems[:200]:
        print("PROBLEM:", p)
    print(json.dumps(dict(stats, problems=len(problems)), indent=2))
    print(f"{n} envelopes checked, {len(problems)} problem(s).")
    return 1 if problems else 0


def cmd_viewer(a) -> int:
    from .viewer import write_viewer
    cards = jsonl.read(a.profiles)
    report = json.loads(Path(a.report).read_text(encoding="utf-8")) if a.report and Path(a.report).exists() else {}
    write_viewer(cards, report, Path(a.html))
    print(f"Wrote {a.html} ({len(cards)} companies).")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="norway_agent", description="Signalpost Norwegian company agent")
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="build one terminal envelope per organisation number")
    r.add_argument("--organisations", "--organizations", help="batch file: JSONL (official), JSON, CSV or TXT")
    r.add_argument("--input", "-i", help="alias of --organisations")
    r.add_argument("--output", help="envelopes JSONL path (default <out>/envelopes.jsonl)")
    r.add_argument("--out", "-o", help="output folder (default ./out)")
    r.add_argument("--report", help="run report JSON (default next to the envelopes)")
    r.add_argument("--profiles-output", help="profile cards JSONL for UIs (default <out>/profiles.jsonl)")
    r.add_argument("--previous", help="previous envelopes JSONL to diff against (default: the existing output)")
    r.add_argument("--run-id")
    r.add_argument("--expected-count", type=int)
    r.add_argument("--max-requests", type=int, help="hard request cap (default 18 x companies + 200, "
                                                    "or $SIGNALPOST_MAX_REQUESTS)")
    r.add_argument("--deadline-min", type=float, help="wall-clock limit (default 40, or $SIGNALPOST_DEADLINE_SECONDS)")
    r.add_argument("--no-web", action="store_true")
    r.add_argument("--no-jobs", action="store_true")
    r.add_argument("--no-activity", action="store_true")
    r.add_argument("--no-guess", action="store_true", help="do not try <name>.no when no website is declared")
    r.add_argument("--registry-only", action="store_true")
    r.add_argument("--no-viewer", action="store_true")
    r.add_argument("--bulk", help="accepted for starter-kit compatibility; not needed (identity is batched live)")
    r.set_defaults(fn=cmd_run)
    v = sub.add_parser("validate", help="check contract shape and replay evidence spans from snapshots")
    v.add_argument("--output", help="envelopes JSONL")
    v.add_argument("--out", "-o")
    v.add_argument("--organisations", help="batch file, to check one envelope per input")
    v.set_defaults(fn=cmd_validate)
    w = sub.add_parser("viewer", help="rebuild the offline viewer (index.html) from an existing profiles.jsonl")
    w.add_argument("--profiles", default="submission/profiles.jsonl")
    w.add_argument("--report", default="submission/run-report.json")
    w.add_argument("--html", default="submission/index.html")
    w.set_defaults(fn=cmd_viewer)
    a = ap.parse_args(argv)
    return a.fn(a)
