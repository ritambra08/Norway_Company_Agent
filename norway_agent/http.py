"""Budgeted, rate-limited HTTP client shared by every collector.

Every outbound request goes through ``Http.get`` so the global request budget, the wall-clock
deadline, per-company caps and per-lane politeness are enforced in one place. Redirects are
followed manually so each hop is counted and the redirect chain is recorded as evidence.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import random
import re
import socket
import threading
import time
import urllib.error
import urllib.request
import zlib
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urljoin, urlsplit

from .config import Config

RETRY_STATUS = {429, 502, 503, 504}


def _permanent_network_error(e: BaseException) -> bool:
    """Errors that a retry cannot fix: the host name does not exist, the certificate is invalid,
    the port is closed, or the URL itself is malformed. Retrying these only burns time and budget
    (most come from guessed domains that were never registered)."""
    reason = getattr(e, "reason", e)
    if isinstance(reason, socket.gaierror):
        return getattr(reason, "errno", None) != getattr(socket, "EAI_AGAIN", object())
    return isinstance(reason, (ConnectionRefusedError, ValueError))  # ValueError covers SSL cert errors


class BudgetExceeded(RuntimeError):
    """Raised before a request that would break a run limit."""


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass
class Response:
    url: str
    final_url: str
    status: int  # 0 = network error, no HTTP response
    headers: dict
    body: bytes
    retrieved_at: str
    sha256: str
    redirects: list = field(default_factory=list)
    elapsed_ms: int = 0
    error: str | None = None
    truncated: bool = False
    snapshot: str | None = None  # relative path once stored

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    @property
    def content_type(self) -> str:
        return (self.headers.get("content-type") or "").split(";")[0].strip().lower()

    def _decode(self) -> str:
        charset = None
        m = re.search(r"charset=([\w\-]+)", self.headers.get("content-type") or "", re.I)
        if m:
            charset = m.group(1)
        if not charset:
            m = re.search(rb"<meta[^>]+charset=[\"']?([\w\-]+)", self.body[:4096], re.I)
            if m:
                charset = m.group(1).decode("ascii", "ignore")
        for enc in (charset, "utf-8", "cp1252"):
            if not enc:
                continue
            try:
                return self.body.decode(enc)
            except (LookupError, UnicodeDecodeError):
                continue
        return self.body.decode("utf-8", "replace")

    def json(self):
        cache = self.__dict__.get("_json")
        if cache is not None:
            return cache[0]
        try:
            val = json.loads(self.body.decode("utf-8-sig")) if self.body else None
        except ValueError:
            val = None
        self.__dict__["_json"] = (val,)
        return val

    def text(self) -> str:  # noqa: F811 - cached wrapper around _decode
        cache = self.__dict__.get("_text")
        if cache is None:
            cache = self._decode()
            self.__dict__["_text"] = cache
        return cache


@dataclass
class Scope:
    """Per-company request allowance and attribution."""

    limit: int
    used: int = 0
    elapsed_ms: int = 0


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # noqa: D401 - urllib hook
        return None


def _app_error(raw: bytes) -> bool:
    """A 500 whose JSON body carries a 'message' is a deterministic refusal, not a blip."""
    try:
        body = json.loads(raw)
    except ValueError:
        return False
    return isinstance(body, dict) and isinstance(body.get("message"), str)


def _decompress(body: bytes, encoding: str) -> bytes:
    enc = (encoding or "").lower()
    try:
        if "gzip" in enc:
            return gzip.decompress(body)
        if "deflate" in enc:
            try:
                return zlib.decompress(body)
            except zlib.error:
                return zlib.decompress(body, -zlib.MAX_WBITS)
    except (OSError, zlib.error, EOFError):
        return body
    return body


class Http:
    def __init__(self, cfg: Config, opener=None, clock=time.monotonic, sleep=time.sleep):
        self.cfg = cfg
        self.max_requests = int(cfg.max_requests or 2000)
        self._open = opener or self._urllib_open
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._next_slot: dict[str, float] = {}
        self.started = clock()
        self.requests = 0
        self.errors = 0
        self.by_lane: Counter = Counter()
        self._opener = urllib.request.build_opener(_NoRedirect)

    # ---- budget -------------------------------------------------------------------------
    def elapsed(self) -> float:
        return self._clock() - self.started

    def time_left(self) -> float:
        return self.cfg.deadline_seconds - self.elapsed()

    def remaining(self) -> int:
        return self.max_requests - self.requests

    def can_start(self, reserve_requests: int = 0) -> bool:
        return self.time_left() > self.cfg.finalize_reserve_seconds and self.remaining() > reserve_requests

    def _interval(self, lane: str) -> float:
        if lane == "brreg":
            return 1.0 / self.cfg.api_rps
        if lane == "nav":
            return 1.0 / self.cfg.nav_rps
        return self.cfg.web_host_interval

    def _reserve(self, lane_key: str, lane: str, scope: Scope | None) -> None:
        with self._lock:
            if self.time_left() <= self.cfg.finalize_reserve_seconds:
                raise BudgetExceeded("time budget exhausted")
            if self.requests >= self.max_requests:
                raise BudgetExceeded("request budget exhausted")
            if scope is not None and scope.used >= scope.limit:
                raise BudgetExceeded("per-company request cap reached")
            self.requests += 1
            self.by_lane[lane] += 1
            if scope is not None:
                scope.used += 1
            now = self._clock()
            slot = max(now, self._next_slot.get(lane_key, 0.0))
            self._next_slot[lane_key] = slot + self._interval(lane)
            wait = slot - now
        if wait > 0:
            self._sleep(wait)

    # ---- transport ----------------------------------------------------------------------
    def _urllib_open(self, url: str, headers: dict, timeout: float, max_bytes: int):
        req = urllib.request.Request(url, headers=headers)
        try:
            with self._opener.open(req, timeout=timeout) as r:
                raw = r.read(max_bytes + 1)
                return r.status, dict(r.headers), raw
        except urllib.error.HTTPError as e:  # 3xx (no auto-redirect), 4xx, 5xx carry headers/body
            try:
                raw = e.read(max_bytes + 1)
            except Exception:  # noqa: BLE001 - body is optional
                raw = b""
            return e.code, dict(e.headers or {}), raw

    def get(self, url: str, *, lane: str, scope: Scope | None = None, headers: dict | None = None,
            timeout: float | None = None, attempts: int | None = None, max_bytes: int | None = None,
            follow: bool = True, max_hops: int = 5) -> Response:
        is_web = lane == "web"
        timeout = timeout or (self.cfg.timeout_web if is_web else self.cfg.timeout_api)
        attempts = attempts or (self.cfg.web_attempts if is_web else self.cfg.api_attempts)
        max_bytes = max_bytes or (self.cfg.max_page_bytes if is_web else self.cfg.max_api_bytes)
        hdrs = {"User-Agent": self.cfg.user_agent, "Accept-Encoding": "gzip, deflate",
                "Accept": "application/json" if not is_web else
                "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.5",
                "Accept-Language": "nb-NO,nb;q=0.9,no;q=0.8,en;q=0.6"}
        hdrs.update(headers or {})
        chain: list[str] = []
        current = url
        t0 = self._clock()
        last_err = "unknown error"
        status, rh, raw = 0, {}, b""
        hops = 0
        while True:
            host = urlsplit(current).hostname or ""
            lane_key = f"web:{host}" if is_web else lane
            ok_response = False
            for attempt in range(attempts):
                self._reserve(lane_key, lane, scope)
                try:
                    status, rh, raw = self._open(current, hdrs, timeout, max_bytes)
                except (urllib.error.URLError, TimeoutError, ConnectionError, OSError, ValueError) as e:
                    last_err = f"network error: {getattr(e, 'reason', e)}"
                    status, rh, raw = 0, {}, b""
                    self.errors += 1
                    if is_web and _permanent_network_error(e):
                        break
                    if attempt + 1 < attempts:
                        self._sleep(min(1.0 * (2 ** attempt) + random.random(), 8.0))
                    continue
                rh = {k.lower(): v for k, v in (rh or {}).items()}
                if (status in RETRY_STATUS or (status == 500 and not _app_error(raw))) and attempt + 1 < attempts:
                    last_err = f"HTTP {status}"
                    self.errors += 1
                    ra = rh.get("retry-after", "")
                    self._sleep(min(float(ra), 20.0) if ra.replace(".", "", 1).isdigit()
                                else min(1.0 * (2 ** attempt) + random.random(), 8.0))
                    continue
                ok_response = True
                break
            if not ok_response and status == 0:
                break
            if follow and status in (301, 302, 303, 307, 308) and rh.get("location") and hops < max_hops:
                chain.append(current)
                current = urljoin(current, rh["location"])
                hops += 1
                continue
            break

        truncated = len(raw) > max_bytes
        raw = raw[:max_bytes]
        body = _decompress(raw, rh.get("content-encoding", "")) if raw else b""
        elapsed_ms = int((self._clock() - t0) * 1000)
        if scope is not None:
            scope.elapsed_ms += elapsed_ms
        return Response(
            url=url, final_url=current, status=status, headers=rh, body=body,
            retrieved_at=utcnow_iso(), sha256=hashlib.sha256(body).hexdigest(), redirects=chain,
            elapsed_ms=elapsed_ms, error=None if status else last_err, truncated=truncated,
        )
