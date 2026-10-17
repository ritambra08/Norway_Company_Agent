from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

SCHEMA_VERSION = "1.0"


@dataclass
class Source:
    name: str
    url: str  # exact API URL that was fetched
    record_url: str  # human-readable page for the same entity
    retrieved_at: str
    json_paths: list[str] = field(default_factory=list)
    source_date: str | None = None  # a date the source itself states (e.g. period end)
    last_modified: str | None = None  # HTTP Last-Modified, when sent
    response_sha256: str | None = None
    raw_file: str | None = None  # saved snapshot, relative to the output dir
    license: str = "NLOD 2.0 (https://data.norge.no/nlod)"
    verified_match: bool = True  # source record's org number == requested one


@dataclass
class Fact:
    key: str
    label: str
    value: Any
    subject_orgnr: str
    source: Source
    explanation: str = ""
    first_seen_at: str | None = None
    last_verified_at: str | None = None
    changed_at: str | None = None


@dataclass
class Profile:
    organisasjonsnummer: str
    status: str  # ok | partial | not_found | deleted | invalid_orgnr | error | skipped_budget
    entity_type: str | None
    retrieved_at: str
    summary: str = ""
    facts: list[Fact] = field(default_factory=list)
    not_found: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    change_log: list[dict] = field(default_factory=list)
    stale: bool = False
    last_error: str | None = None
    schema_version: str = SCHEMA_VERSION
    agent: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)
