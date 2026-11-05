# Norway Company Agent (Signalpost)

Give it a batch of Norwegian organisation numbers. It returns **one terminal envelope per input** with legal identity, latest accounts and history, leadership, workplaces, verified official website, company-owned profiles, hiring, and dated public activity. Every claim carries its source, retrieval time, content hash, reporting period and an evidence span that can be replayed from a stored snapshot.

- **Deterministic.** No LLM, no paid API, no API keys. Third-party cost: **$0**.
- **Standard library only.** Python ≥ 3.10, nothing to install.
- **Exact identity first.** Nothing is published unless it is tied to the exact organisation number.

## Run it (one command)

```bash
./run.sh batch.jsonl out
# same thing:
python3 -m norway_agent run --organisations batch.jsonl --output out/envelopes.jsonl --report out/run-report.json --run-id <id>
```

- **Input format:** JSONL is the official format, e.g. `{"organisation_number": "923609016"}`. JSON, CSV and plain text also work, and so do `.gz` files. Recognised keys are `organisation_number`, `organisasjonsnummer` and `orgnr`; a row with any other single 9-digit field also works.
- **Starter-kit form:** `python3 scripts/run_competition_batch.py --organisations … --output … --report … --run-id … --expected-count N`. It accepts the same flags as the reference agent; `--bulk` is accepted and not needed.
- **Install step:** none. `uv sync` also works and installs nothing.
- **Try it:** `./run.sh examples/sample-batch.jsonl out`, then open `out/index.html`.
- **Self-check:** `python3 -m norway_agent validate --output out/envelopes.jsonl --organisations batch.jsonl`.
- **Smoke test (100 companies from the official universe):** `make smoke`. Results are written to `submission/`.

### Outputs (`out/`)

| File | What |
|---|---|
| `envelopes.jsonl` | One envelope per input, in input order (the scored output; schema below). |
| `run-report.json` | Requests by source, runtime, p50/p95 per-company latency, cost ($0), coverage per field family and per field, changes by type, and the one-envelope-per-input check. |
| `profiles.jsonl` | Flattened profile cards for user interfaces (the JBOX site reads this file). |
| `index.html` | Offline viewer: search, filter, sort, compare up to 3 companies, and open the evidence behind every fact. Works on desktop and mobile. |
| `snapshots/` | Immutable gzip snapshots of every response, plus `index.jsonl` (URL, status, time, hash). |
| `previous-envelopes.jsonl` | The previous run, kept when a rerun overwrites `envelopes.jsonl`. |

## Envelope (Signalpost output contract)

```jsonc
{
  "organisation_number": "923609016",
  "status": "available",                    // one of the six states
  "legal_identity": {"name": "...", "organisation_form": "ASA", "match": "exact organisation number in Enhetsregisteret"},
  "run": {"run_id": "...", "started_at": "...", "completed_at": "...", "terminal_status": "completed|partial|failed"},
  "summary": {"text": "...", "what_changed": "...", "unknowns": [...], "based_on_fields": [...]},
  "availability": {"company_brief": "available", "latest_financials": "...", "who_runs_it": "...",
                   "where_it_operates": "...", "working_here": "...", "recent_activity": "...", "online_presence": "..."},
  "claims": [{"field": "revenue", "family": "financials", "value": {"amount": 914051.0, "currency": "NOK",
              "period_start": "2025-01-01", "period_end": "2025-12-31", "accounts_type": "SELSKAP"},
              "availability": "available", "confidence": 0.99, "evidence_ids": ["ev-…"],
              "reporting_period": {"start": "2025-01-01", "end": "2025-12-31"},
              "first_seen_at": "…", "last_verified_at": "…"},
             {"field": "job_postings", "availability": "not_available", "reason": "…",
              "checked_sources": [{"source": "NAV job vacancy feed …", "status": "checked", "result": "0 active ad(s)"}]}],
  "evidence": [{"id": "ev-…", "source_url": "https://data.brreg.no/regnskapsregisteret/regnskap/…",
                "source_class": "official_accounts", "retrieved_at": "…", "content_sha256": "…",
                "claim_span": "914051.0", "locator": {"type": "json_path", "path": "[2].resultatregnskapResultat…"},
                "reporting_period": {…}, "source_ref": "src-…"}],
  "sources": {"src-…": {"fetched_url": "…", "snapshot": "snapshots/ab/….gz", "licence": "NLOD 2.0", "redirect_chain": […]}},
  "changes": [{"type": "new_role", "field": "leadership", "before": null, "after": {…}, "material": true,
               "evidence_ids": […], "previous_evidence_ids": […]}],
  "errors": [], "abstentions": [], "refresh": {"previous_run_id": "…", "carried_forward": []},
  "operations": {"requests": 7, "runtime_ms": 8120, "third_party_cost_usd": 0}
}
```

- **Availability states:** `available`, `not_available`, `blocked`, `not_applicable`, `ambiguous`, `failed`. Every claim that is not `available` carries a `reason`. Absence is never written as zero.
- **Checked-zero vs not checked:** a source that was checked and had nothing is reported differently from a source that was not checked. Job claims list `checked_sources` with each source's status and result.
- **Abstentions:** listed separately in `abstentions`. An example is a declared website that turns out to belong to the parent company.

## Fields

| Family (section) | Fields | Source |
|---|---|---|
| Company brief | legal name, legal form, register status (active, bankrupt, liquidation…), registration and founding dates, NACE industries, sector, registered activity and purpose, employees (exact count, or 0–4 when the register withholds it), VAT and business-register flags with dates, share capital, group membership, former names, public brand name, website self-description | Enhetsregisteret; verified website |
| Latest financials | latest accounts (period, currency, SELSKAP/KONSERN); revenue, operating result, profit before tax, net result, assets, equity, liabilities and personnel costs, copied verbatim; history for every period in the open API; revenue change (derived, with both figures cited) | Regnskapsregisteret |
| Who runs it | all current roles; CEO (owner for sole proprietorships); chair; board; auditor; accountant | Enhetsregisteret roles |
| Where it operates | business and postal address; registered workplaces (establishments with their own org numbers, addresses and employees); addresses in the website's structured data; contact details | Enhetsregisteret; verified website |
| Online presence | website declared in the register; **verified** official website; LinkedIn, Facebook, Instagram, X, YouTube and TikTok profiles linked from the verified site | Verified website |
| Working here | open job postings and hiring signal; careers page | NAV job feed (tied by org number); careers page (JSON-LD `JobPosting`, ATS links) |
| Recent activity | merged, newest-first timeline: register change events (e.g. "annual accounts for 2025 registered", address/name changes), role updates, name changes, job postings, website news (RSS, `Article` structured data, dated lists) | Enhetsregisteret change feeds; NAV; verified website |

## How facts stay correct (wrong-company guards)

1. **Identity.** A register record is used only if its own `organisasjonsnummer` equals the requested one. Batched lookups are matched by exact number. Establishments keep their own numbers and are never merged with the parent.
2. **Website identity gate.** A site is published as the official website only if one of these holds:
   - it shows this org number on the homepage, contact, about or privacy page, and the page is not a directory listing many org numbers;
   - it was declared for this company by the register (`hjemmeside`) or by the employer homepage on a NAV ad already tied to this org number, **and** the full company name appears in the site's title, headings, footer, © line or structured data (or the domain and the site's brand are both made of the legal name's words). The name appearing in the body text only counts together with the registered phone or email;
   - it is the domain of the company's own registered email, the domain is the legal name itself (`gangstadbygg.no` for GANGSTAD BYGG AS), and the site names the company;
   - it is a guessed domain spelled exactly like the legal name (`.no` first, then `.com`; tried when nothing else verified), and it shows the org number (the agent also checks `/kontakt`, `/om-oss` and `/personvern`, where Norwegian sites usually print it) or the name **plus** the registered phone or email.

   An email address at an employer, accountant, property manager or service provider is never treated as the company's website; this came from a live test where such addresses led to the wrong organisation. Parked or placeholder pages, and the domain name merely appearing in the page text, never count. A site showing a **different** org number is never merged: register-declared sites become `ambiguous` (likely a parent, franchise or sister company), and other candidates are discarded.
3. **Jobs.** A NAV ad is published only if the ad's own `employer.orgnr` is this company, or one of its establishments as confirmed in the register. Same-name employers are rejected and counted. A careers-page `JobPosting` whose `hiringOrganization` names another employer is dropped.
4. **Numbers.** Values are copied verbatim with currency and period, never converted or rounded. Conflicting filings for one period produce `ambiguous` and nothing is published. A missing currency means nothing is published.
5. **Evidence.** Every evidence item points to a stored snapshot plus a JSON path, or a verbatim text span. `validate` re-hashes each snapshot and replays every span. On the fixture and real-site replays: 0 problems.
6. **Privacy.**
   - Birth dates in role data are never copied.
   - Contact persons in job ads are never copied, and inactive ads are dropped (NAV terms).
   - Profile pages are linked, not scraped.

## Refresh and change detection

`--previous` (or an existing `envelopes.jsonl` in the output folder) turns a run into a refresh:

- **Idempotent.** Claim values never contain retrieval times, so the same sources produce zero changes. This is covered by a test.
- **Typed changes,** each with before/after values and both sets of evidence ids: `new_role`, `removed_role`, `new_job`, `closed_job`, `new_location`, `closed_location`, `new_filing`, `restated_accounts`, `changed_name`, `changed_address`, `changed_status`, `changed_website`, `new_profile`, `new_activity`, `availability_changed`, and so on.
- **No silent erasure.** A source that fails during refresh keeps the previous claim with its original evidence, marked `stale` and `carried_forward_from`. Earlier evidence objects are preserved in the envelope.

## Budgets, time and cost

| Limit | Default | Override |
|---|---|---|
| Requests | 18 × companies + 200 (2,000 for 100; 18,200 for 1,000) | `--max-requests` or `SIGNALPOST_MAX_REQUESTS` |
| Wall clock | 40 min, with 60 s reserved to write outputs | `--deadline-min` or `SIGNALPOST_DEADLINE_SECONDS` |
| Website pages | ≤ 9 per company, 1 req/s per host, robots.txt honoured | — |
| Register API | 8 req/s, batched by 100 for identity and change events | — |
| Third-party cost | **$0.00** | — |

- **Breadth-first by source.** Collection runs identity → change events → accounts → roles → establishments → NAV jobs → websites. If a budget is hit, the tail of one source degrades; whole companies never drop. Every input still gets a terminal envelope, and the unchecked sources are marked `failed` with reason "not checked".
- **Scale test (1,100 synthetic companies):** 3,655 requests, about 12 minutes simulated at the rate limits, 9 s of CPU. All envelopes validated.
- **Measured live (100-company smoke test, Nov 2026):** 532 requests and about 4 minutes in total, with 0 validation problems. Expect about 3 register requests per company, 20–40 NAV feed pages per batch (185-day window, 10,000 entries per page), and 3–9 website requests for each company with a site.

## Sources and rights

See [docs/SOURCES_AND_RIGHTS.md](docs/SOURCES_AND_RIGHTS.md): licences, the outbound URL policy, robots handling and secrets.

- No secrets are needed.
- Optional `NAV_FEED_TOKEN`: a registered NAV consumer token, read only from the environment. Without it, the agent uses NAV's published public token.

## Limitations (stated, not hidden)

- **Static HTML only.** JavaScript-only career sites and Finn.no-only job ads are not seen. Job claims therefore say "not found in checked sources" instead of claiming the company is not hiring.
- **No search engine.** Websites come from official declarations, the company email domain, or a `<name>.no` candidate. A licensed search API could raise website recall; it would be a declared, paid connector.
- **No reviews, ratings or sentiment.** These are excluded from current scoring, and no licensed feed is used.
- **Accounts API coverage.** The open accounts API does not serve some filers (banks, insurers, some special layouts); this is reported per company.

## Layout

| Stage | Module |
|---|---|
| Input | `inputs.py` |
| Register | `registry.py` |
| Jobs | `nav.py` |
| Websites | `web.py` (with `htmlparse.py`) |
| Claims | `extract.py` builds claims with `claims.py` |
| Output | `envelope.py`, `refresh.py`, `summary.py` |
| Viewer | `viewer.py` |
| Orchestration | `pipeline.py` |
| Shared | `http.py` (budgeted client), `snapshots.py` (evidence store), `validate.py` (replay) |

Tests (`make test`) run the whole pipeline against a simulated internet: the register, NAV, and websites with namesakes, parent sites, robots blocks, budget exhaustion and refresh.

## JBOX

The public Signalpost site (find a company, compare two, check where each fact came from, on laptop and phone) is built with [JBOX](https://jboxai.com) on top of `submission/profiles.jsonl`. It reads that file directly from this repository, so it updates with each pushed run.

- Brief given to JBOX: [docs/jbox/SIGNALPOST_JBOX_BRIEF.md](docs/jbox/SIGNALPOST_JBOX_BRIEF.md)
- Data reference: [docs/jbox/DATA_SPEC.md](docs/jbox/DATA_SPEC.md)
- Live site (built with JBOX): <JBOX site URL>
