"""Persistence and the update logic that keeps profiles current without losing good data."""

from __future__ import annotations

import json
from pathlib import Path

from .http import Snapshot

FAILED = {"error", "skipped_budget"}


def canon(v) -> str:
    return json.dumps(v, sort_keys=True, ensure_ascii=False)


class Store:
    def __init__(self, out: Path):
        self.out = Path(out)
        (self.out / "profiles").mkdir(parents=True, exist_ok=True)

    def profile_path(self, orgnr: str) -> Path:
        safe = "".join(c for c in orgnr if c.isalnum()) or "unknown"
        return self.out / "profiles" / f"{safe}.json"

    def load_profile(self, orgnr: str) -> dict | None:
        p = self.profile_path(orgnr)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            return None

    def save_profile(self, profile: dict) -> None:
        self.profile_path(profile["organisasjonsnummer"]).write_text(
            json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def save_raw(self, orgnr: str, snaps: dict[str, Snapshot]) -> None:
        d = self.out / "raw" / orgnr
        d.mkdir(parents=True, exist_ok=True)
        for kind, s in snaps.items():
            (d / f"{kind}.json").write_text(
                json.dumps(
                    {"url": s.url, "status": s.status, "retrieved_at": s.retrieved_at,
                     "last_modified": s.last_modified, "sha256": s.sha256, "body": s.body},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

    def load_raw(self, orgnr: str) -> dict[str, Snapshot]:
        snaps: dict[str, Snapshot] = {}
        d = self.out / "raw" / orgnr
        if d.is_dir():
            for f in sorted(d.glob("*.json")):
                e = json.loads(f.read_text(encoding="utf-8"))
                snaps[f.stem] = Snapshot(e["url"], e["status"], e["retrieved_at"], e["last_modified"], e["sha256"], e["body"])
        return snaps


def apply_update(new: dict, prev: dict | None, now: str) -> dict:
    """Merge a fresh profile with the stored one.

    * first run: stamp first_seen / last_verified;
    * fresh data: unchanged facts keep first_seen, changed ones get changed_at and a
      change_log entry, vanished ones are logged as removed;
    * failed or partial run: never wipe good stored data, mark it stale instead.
    """
    if prev is None or not prev.get("facts"):
        for f in new["facts"]:
            f["first_seen_at"] = f["last_verified_at"] = now
        return new

    if new["status"] in FAILED:
        kept = dict(prev)
        kept["stale"] = True
        kept["last_error"] = "; ".join(new.get("notes") or []) or new.get("last_error") or new["status"]
        return kept

    prev_facts = {f["key"]: f for f in prev["facts"]}
    log = list(prev.get("change_log") or [])[-49:]
    new_keys = set()
    for f in new["facts"]:
        new_keys.add(f["key"])
        old = prev_facts.get(f["key"])
        f["last_verified_at"] = now
        if old is None:
            f["first_seen_at"] = now
            log.append({"at": now, "type": "added", "key": f["key"], "new": f["value"]})
        elif canon(old["value"]) == canon(f["value"]):
            f["first_seen_at"] = old.get("first_seen_at") or now
            f["changed_at"] = old.get("changed_at")
        else:
            f["first_seen_at"] = now
            f["changed_at"] = now
            log.append({"at": now, "type": "changed", "key": f["key"], "old": old["value"], "new": f["value"],
                        "previous_retrieved_at": old["source"]["retrieved_at"]})

    if new["status"] == "partial":
        # Keys we did not manage to re-check stay, flagged stale, instead of disappearing.
        for k, old in prev_facts.items():
            if k not in new_keys:
                new["facts"].append(old)
        new["stale"] = True
    else:
        for k, old in prev_facts.items():
            if k not in new_keys:
                log.append({"at": now, "type": "removed", "key": k, "old": old["value"]})

    if prev.get("status") != new["status"]:
        log.append({"at": now, "type": "status_change", "from": prev.get("status"), "to": new["status"]})
    new["change_log"] = log
    return new
