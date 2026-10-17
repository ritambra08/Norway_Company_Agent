# Norway Company Agent

Give it Norwegian organisation numbers; it returns one evidence-linked profile per company, built only from Brønnøysund Register Centre open data (NLOD licence). Standard library only, no API keys, no paid services.

## Run it

```bash
python3 scripts/sample_orgnrs.py --n 1000 > companies.txt     # one-off: 1,000 random org numbers
./run.sh companies.txt out                                     # the one command
# equivalent: python3 -m norway_agent run --input companies.txt --out out
python3 -m norway_agent validate --out out                     # self-check (replays raw snapshots)
```

Single numbers work too: `python3 -m norway_agent run 923609016 "984 851 006"`.
Input can be txt/csv (one number per line, spaces/`NO…MVA` tolerated) or JSON/JSONL.

Output (`out/`): `profiles.jsonl` (one profile per line, input order), `profiles/<orgnr>.json`, `raw/<orgnr>/*.json` (exact API responses), `manifest.json` (requests, time, cost, status counts).

## What a profile contains

| Source | Facts |
|---|---|
| Enhetsregisteret `/enheter/{orgnr}` (or `/underenheter/{orgnr}`) | name, organisation form, registration/founding dates, status flags, NACE codes, sector, employees, addresses, website/email/phone, VAT & other register flags, purpose/activity text, parent unit |
| `/enheter/{orgnr}/roller` | current role holders (names + role only; birth dates are never copied) |
| `/underenheter?overordnetEnhet=` | establishments with their own org numbers |
| Regnskapsregisteret `/regnskap?orgNummer=` | latest period: revenue, operating result, result before tax, net result, operating and personnel costs, equity, liabilities, total/current/fixed assets, currency, period |

Each fact: `value`, `subject_orgnr`, `source.url` (exact API call), `record_url` (human page), `retrieved_at`, `source_date` (period end for accounts), `last_modified`, `response_sha256`, `raw_file`, `json_paths`, plus `explanation`, `first_seen_at`, `last_verified_at`, `changed_at`. Gaps go in `not_found` with a reason. `summary` is a plain-language paragraph written only from the verified facts. `change_log` records every added/changed/removed fact between runs.

## Why the facts are safe

- **Right company.** A record is used only if its own `organisasjonsnummer` equals the one asked for; otherwise nothing is published and no further calls are made. Accounts and establishment rows that belong to another number are discarded and noted. Establishments (underenheter) have their own numbers and are never merged with their parent. Org numbers are checksum-validated (MOD-11) before any request.
- **No invented numbers.** Financial values are copied verbatim with currency and period. No estimating, converting or rounding. Conflicting accounts for one period, or a missing currency, means *no* financial facts plus a reason. "Employees not registered" is reported as missing, never as 0.
- **Replayable.** `validate` re-derives every fact from the saved raw responses and fails on any difference, and checks each fact's subject, source host, date and financial currency/period.
- **Updates.** A rerun diffs against the stored profile. A failed or partial refresh never overwrites good data; it marks the profile `stale`.

## Limits and cost

| Limit | Setting | Why it is safe |
|---|---|---|
| 2,000 requests | hard stop at 1,800 (`--max-requests`); ≤ 8 per company; retries are counted | ≤ ~5 requests per company (accounts lookup skipped for sole proprietorships, state, county, municipality) → 100 companies ≈ 500 |
| 45 minutes | hard stop at 40 (`--deadline-min`); 8 req/s, 6 workers | 100 companies ≈ 1–2 min; 1,000 ≈ 10 min |
| $10 API cost | declared **$0.00** | no LLM and no paid API is called |

Hitting a limit yields `partial`/`skipped_budget` profiles, never a crash. The 1,000-profile submission run needs `--max-requests 6000` (about 5,000 requests, ~10 min); the default cap protects the daily test.

**Model/API details:** none. The pipeline is deterministic (HTTP + JSON parsing). Public APIs used: `data.brreg.no/enhetsregisteret/api` and `data.brreg.no/regnskapsregisteret/regnskap`. Both are free and keyless.

## Known caveats

- Not yet run against the live API (the authoring sandbox could not reach brreg.no). Tests use synthetic fixtures. **First live run: do 5–10 companies and inspect `out/raw/` before the full run.**
- The Enhetsregisteret field names come from the public data model, not a fetched spec (the docs page blocked automated fetching). If a field is missing in `raw/`, adjust `extract.py`.
- The Regnskapsregisteret open API is documented as a temporary R&D service that can be withdrawn and covers only companies using the ordinary accounts layout; the lookup parameter (`orgNummer`) follows the published spec. If it returns nothing, profiles degrade to registry facts with a clear `not_found` reason.
- Phone/email/names are published in the open register; for sole proprietorships (ENK) they may be personal data. Use `--no-roles` or filter before sharing if that matters to you.

## Layout

`collect.py` (network) → `extract.py` (pure, snapshots → facts) → `explain.py` (summary) → `store.py` (persist + update diff) → `build.py` (orchestrates one company) → `cli.py` (+ `inputs.py`, `http.py` budgeted client). `validate.py` replays `extract.py` on `raw/`. Tests: `make test`.
