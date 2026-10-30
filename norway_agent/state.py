"""Per-company working state collected across pipeline stages."""

from __future__ import annotations

from dataclasses import dataclass, field

from .http import Response, Scope


@dataclass
class Fetch:
    """A response plus the JSON path of this company's record inside it (for batched calls)."""

    resp: Response
    path: str = ""
    data: object = None


@dataclass
class SourceStatus:
    """What happened when a source was checked for this company (drives availability states)."""

    state: str  # checked | not_found | blocked | failed | not_checked | not_applicable
    detail: str = ""


@dataclass
class CompanyState:
    input: str
    orgnr: str | None
    valid: bool
    scope: Scope
    kind: str | None = None  # "enhet" | "underenhet"
    record: Fetch | None = None
    identity: SourceStatus = field(default_factory=lambda: SourceStatus("not_checked"))
    roles: Fetch | None = None
    subunits: Fetch | None = None
    accounts: Fetch | None = None
    status: dict = field(default_factory=dict)  # source key -> SourceStatus
    updates: list = field(default_factory=list)  # Fetch per event
    role_events: list = field(default_factory=list)
    nav_ads: list = field(default_factory=list)  # Fetch per confirmed ad (data = ad dict, path = relation)
    nav_rejected: int = 0
    web: dict = field(default_factory=dict)  # see web.py
    errors: list = field(default_factory=list)

    def name(self) -> str:
        if self.record and isinstance(self.record.data, dict):
            return self.record.data.get("navn") or ""
        return ""

    def body(self) -> dict:
        return self.record.data if self.record and isinstance(self.record.data, dict) else {}

    def subunit_list(self) -> list[dict]:
        if not self.subunits or not isinstance(self.subunits.data, dict):
            return []
        emb = (self.subunits.data.get("_embedded") or {}).get("underenheter") or []
        return [u for u in emb if isinstance(u, dict) and u.get("overordnetEnhet") == self.orgnr]

    def error(self, source: str, message: str) -> None:
        self.errors.append({"source": source, "message": message})
