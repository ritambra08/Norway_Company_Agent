"""Budgeted, rate-limited JSON HTTP client.

Every outbound request goes through ``Http.get_json`` so the request count,
the wall-clock deadline and the per-company cap are enforced in one place.
"""

from __future__ import annotations

import hashlib
import json
import random
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone

from .config import Config

RETRY_STATUS = {429, 500, 502, 503, 504}


class BudgetExceeded(RuntimeError):
    """Raised before a request that would break a run limit."""


class HttpError(RuntimeError):
    """Raised when a request keeps failing after retries."""


def _is_app_error(raw: bytes) -> bool:
    """A 500 whose JSON body carries a 'message' is a deterministic refusal, not a blip."""
    try:
        body = json.loads(raw)
    except ValueError:
        return False
    return isinstance(body, dict) and isinstance(body.get("message"), str)


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass
class Snapshot:
    url: str
    status: int
    retrieved_at: str
    last_modified: str | None
    sha256: str
    body: object  # parsed JSON, or None when the body was empty / not JSON


@dataclass
class Scope:
    """Per-company request allowance."""

    limit: int
    used: int = 0


class Http:
    def __init__(self, cfg: Config, opener=None, clock=time.monotonic, sleep=time.sleep):
        self.cfg = cfg
        self._open = opener or self._urllib_open
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._next_slot = 0.0
        self.started = clock()
        self.requests = 0
        self.errors = 0

    @staticmethod
    def _urllib_open(url: str, headers: dict, timeout: float):
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:  # 4xx/5xx still carry a body we want
            return e.code, dict(e.headers or {}), e.read()

    def elapsed(self) -> float:
        return self._clock() - self.started

    def _reserve(self, scope: Scope | None) -> None:
        with self._lock:
            if self.elapsed() >= self.cfg.deadline_seconds:
                raise BudgetExceeded("time budget exhausted")
            if self.requests >= self.cfg.max_requests:
                raise BudgetExceeded("request budget exhausted")
            if scope is not None and scope.used >= scope.limit:
                raise BudgetExceeded("per-company request cap reached")
            self.requests += 1
            if scope is not None:
                scope.used += 1
            now = self._clock()
            slot = max(now, self._next_slot)
            self._next_slot = slot + 1.0 / self.cfg.requests_per_second
            wait = slot - now
        if wait > 0:
            self._sleep(wait)

    @staticmethod
    def _backoff(attempt: int, retry_after: str | None) -> float:
        if retry_after:
            try:
                return min(float(retry_after), 30.0)
            except ValueError:
                pass
        return 1.0 * (2**attempt) + random.random()

    def get_json(self, url: str, scope: Scope | None = None) -> Snapshot:
        headers = {
            "Accept": "application/json",
            "User-Agent": self.cfg.user_agent,
            "Accept-Encoding": "identity",
        }
        last = "unknown error"
        for attempt in range(self.cfg.max_attempts):
            self._reserve(scope)
            try:
                status, hdrs, raw = self._open(url, headers, self.cfg.timeout)
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
                last = f"network error: {e}"
                self.errors += 1
                self._sleep(self._backoff(attempt, None))
                continue
            hdrs = {k.lower(): v for k, v in hdrs.items()}
            if status in RETRY_STATUS and not (status == 500 and _is_app_error(raw)):
                last = f"HTTP {status}"
                self.errors += 1
                self._sleep(self._backoff(attempt, hdrs.get("retry-after")))
                continue
            body = None
            if raw:
                try:
                    body = json.loads(raw)
                except ValueError:
                    if status == 200:
                        raise HttpError(f"{url}: response was not valid JSON")
            return Snapshot(
                url=url,
                status=status,
                retrieved_at=utcnow_iso(),
                last_modified=hdrs.get("last-modified"),
                sha256=hashlib.sha256(raw or b"").hexdigest(),
                body=body,
            )
        raise HttpError(f"{url}: {last} after {self.cfg.max_attempts} attempts")
