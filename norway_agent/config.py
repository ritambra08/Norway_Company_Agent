from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    base_url: str = "https://data.brreg.no"
    user_agent: str = "norway-company-agent/0.1 (public-data research agent)"

    # Hard limits from the challenge: 45 min, 2,000 outbound requests, $10 API cost.
    # We stay well inside them so a slow API never turns into a failed run.
    max_requests: int = 1800
    deadline_seconds: float = 40 * 60
    max_requests_per_company: int = 8
    declared_api_cost_usd: float = 0.0  # no paid API is called

    requests_per_second: float = 8.0
    workers: int = 6
    timeout: float = 20.0
    max_attempts: int = 3

    include_roles: bool = True
    include_subunits: bool = True
    include_financials: bool = True


# Organisation forms that do not file annual accounts with Regnskapsregisteret
# (sole proprietorships, state, county, municipality, organisation branches),
# so we do not spend a request looking for them.
NO_ACCOUNTS_FORMS = frozenset({"ENK", "STAT", "FYLK", "KOMM", "ORGL"})
