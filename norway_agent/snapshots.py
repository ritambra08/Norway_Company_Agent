"""Immutable, content-addressed snapshots of every response used as evidence.

Snapshots are stored before extraction (gzip, deterministic header) under
``<out>/snapshots/<sha[:2]>/<sha>.gz``. ``index.jsonl`` records URL, status and retrieval time
for each capture so any claim can be replayed and its evidence span re-checked.
"""

from __future__ import annotations

import gzip
import io
import json
import threading
from pathlib import Path

from .http import Response


class SnapshotStore:
    def __init__(self, out_dir: Path):
        self.out = Path(out_dir)
        self.root = self.out / "snapshots"
        self.root.mkdir(parents=True, exist_ok=True)
        self.index_path = self.root / "index.jsonl"
        self._lock = threading.Lock()

    def put(self, resp: Response) -> str:
        sha = resp.sha256
        rel = f"snapshots/{sha[:2]}/{sha}.gz"
        path = self.out / rel
        with self._lock:
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                buf = io.BytesIO()
                with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
                    gz.write(resp.body)
                tmp = path.with_suffix(".tmp")
                tmp.write_bytes(buf.getvalue())
                tmp.replace(path)
            with self.index_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({
                    "sha256": sha, "url": resp.url, "final_url": resp.final_url, "status": resp.status,
                    "retrieved_at": resp.retrieved_at, "content_type": resp.content_type,
                    "bytes": len(resp.body), "redirects": resp.redirects, "path": rel,
                }, ensure_ascii=False) + "\n")
        resp.snapshot = rel
        return rel


def read_snapshot(out_dir: Path, rel: str) -> bytes | None:
    p = Path(out_dir) / rel
    if not p.exists():
        return None
    return gzip.decompress(p.read_bytes())
