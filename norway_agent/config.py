"""Run configuration. Every limit can be set by flag or environment variable."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env_int(name: str) -> int | None:
    v = os.environ.get(name, "").strip()
    return int(v) if v.isdigit() else None


def _env_float(name: str) -> float | None:
    v = os.environ.get(name, "").strip()
    try:
        return float(v) if v else None
    except ValueError:
        return None


@dataclass
class Config:
    brreg_base: str = "https://data.brreg.no"
    nav_base: str = "https://pam-stilling-feed.nav.no"
    user_agent: str = ("SignalpostCompanyAgent/0.2 (+https://github.com/ritambra08/Norway_Company_Agent; "
                       "public-data company research; respects robots.txt)")

    # Budgets. ``max_requests`` = None means "scale with the batch": 18 requests per company + 200,
    # i.e. 2,000 for the 100-company daily test and 18,200 for a 1,000-company batch.
    max_requests: int | None = None
    requests_per_company: int = 18
    requests_base: int = 200
    deadline_seconds: float = 40 * 60
    finalize_reserve_seconds: float = 60.0

    # Politeness / concurrency
    api_rps: float = 8.0           # data.brreg.no (no published limit; stay courteous)
    nav_rps: float = 4.0
    web_host_interval: float = 1.0  # seconds between requests to the same website host
    api_workers: int = 6
    web_workers: int = 16
    timeout_api: float = 25.0
    timeout_web: float = 10.0
    api_attempts: int = 3
    web_attempts: int = 1
    max_page_bytes: int = 1_500_000
    max_api_bytes: int = 30_000_000

    # Families (all on by default)
    enable_registry_extras: bool = True   # roles, establishments, accounts
    enable_activity: bool = True          # dated registry change events
    enable_jobs: bool = True              # NAV job-ad feed
    enable_web: bool = True               # official website verification + company-owned pages
    enable_domain_guess: bool = True      # try <name>.no when the register lists no website

    activity_window_days: int = 730
    jobs_window_days: int = 120
    jobs_page_size: int = 5000
    jobs_max_pages: int = 80
    jobs_time_budget_s: float = 420.0
    jobs_max_details_per_company: int = 12
    web_pages_per_company: int = 9
    web_time_budget_share: float = 0.9   # of the time left when the web stage starts

    declared_api_cost_usd: float = 0.0
    extra: dict = field(default_factory=dict)

    def resolve(self, n_companies: int) -> "Config":
        """Apply environment overrides and batch-size scaling. Returns self for chaining."""
        env_max = _env_int("SIGNALPOST_MAX_REQUESTS")
        if self.max_requests is None:
            self.max_requests = env_max or (self.requests_per_company * max(1, n_companies) + self.requests_base)
        env_deadline = _env_float("SIGNALPOST_DEADLINE_SECONDS")
        if env_deadline:
            self.deadline_seconds = env_deadline
        return self


# Organisation forms that never file accounts with Regnskapsregisteret unless the register
# says they did (sisteInnsendteAarsregnskap); we skip the lookup to save a request.
NO_ACCOUNTS_FORMS = frozenset({"STAT", "FYLK", "KOMM", "ORGL"})
