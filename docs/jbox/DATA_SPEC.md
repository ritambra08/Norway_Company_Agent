# Signalpost data file: field reference

Each line of `profiles.jsonl` is one company record. The live file is at
`https://raw.githubusercontent.com/ritambra08/Norway_Company_Agent/main/submission/profiles.jsonl`.
`sample-profiles.jsonl` holds 6 real records in the same format.

## Top level

| Key | Type | Meaning |
|---|---|---|
| `org` | string, 9 digits | Organisation number (the company's unique ID) |
| `name` | string | Legal name, for example `NOVOOI AS` |
| `brand` | string or null | Public brand name from the verified website |
| `form` | string | Legal form code: `AS`, `ASA`, `ENK`, `BRL`, `STI`, … |
| `city` | string | City of the business address |
| `industry` | string or null | Primary industry description (Norwegian) |
| `status` | state | Overall profile state (normally `available`) |
| `registry_status` | string or null | `active`, `bankrupt`, `under_liquidation`, `under_forced_dissolution`, `closed`, `deleted` |
| `revenue`, `net_result` | number or null | Latest filed figures. **null means not found, never zero.** |
| `currency` | string or null | Currency of the figures, for example `NOK` |
| `year` | string or null | Year of the latest accounts |
| `employees` | integer or null | Registered employee count. null can mean "fewer than 5"; see the `registered_employees` fact |
| `website` | string or null | Verified official website URL |
| `jobs` | integer or null | Number of open job postings found (null = none found) |
| `completeness` | number 0–1 | Share of 12 key facts found (used for "Most data found") |
| `summary` | string | Plain-language profile, several lines, written only from the facts |
| `sections` | object | Section key → state, for these 7 sections: `company_brief`, `latest_financials`, `who_runs_it`, `where_it_operates`, `working_here`, `online_presence`, `recent_activity` |
| `facts` | object | Field name → fact (see below) |
| `E` | array | Evidence items; facts point into this list by index |
| `changes` | array | Changes since the previous run (may be empty) |
| `errors` | array | `{source, message}` run notes |
| `run` | string | Run id, for example `smoke-20261105T153906Z` (the date is inside it) |

## A fact

```json
{"label": "Operating revenue", "state": "available", "text": "NOK 541 thousand (2025)",
 "ev": [26, 27], "note": "Copied unchanged from the filed annual accounts; no conversion or rounding."}
```

| Key | Present when | Meaning |
|---|---|---|
| `label` | always | Display label |
| `state` | always | One of the six states: `available`, `not_available`, `ambiguous`, `blocked`, `failed`, `not_applicable` |
| `text` | always | Ready-to-show text; may be `""` for list or object values |
| `value` | available and `text` is empty | Structured value (shapes below) |
| `ev` | available | Integer indexes into `E`: the evidence for this fact |
| `note` | sometimes | Short explanation to show in grey |
| `stale` | sometimes | `true` = kept from the previous run because the source failed this time |
| `reason` | not available | Why it is missing. Show this instead of a value. |

## Evidence item (`E[i]`)

| Key | Meaning |
|---|---|
| `s` | Source name, for example `Brønnøysundregistrene – Regnskapsregisteret` |
| `u` | Source URL (open in a new tab) |
| `t` | Retrieved at (ISO time, UTC) |
| `h` | First 12 characters of the SHA-256 content hash ("content fingerprint") |
| `x` | Quoted text from the source (the exact value or sentence) |
| `l` | Location inside the source (JSON path or extraction method) |
| `p` | `1` = from the previous run |

## Value shapes (for facts where `text` is empty)

| Field | Shape |
|---|---|
| `annual_accounts_latest` | `{period_start, period_end, currency, accounts_type, figures: {revenue, operating_result, profit_before_tax, net_result, total_assets, equity, total_liabilities, personnel_costs}, small_company_rules, audit_opted_out}`. Any figure may be absent. |
| `annual_accounts_history` | List, newest first: `{period_start, period_end, currency, accounts_type, revenue, operating_result, profit_before_tax, net_result, total_assets, equity, total_liabilities}` |
| `leadership`, `board_members` | List of `{role_code, role, group, holder_type: "person"/"organisation", name, holder_orgnr?}` |
| `chief_executive`, `board_chair`, `auditor`, `accountant` | Usually already in `text`; the value is one item as above, or a list of them |
| `registered_workplaces` | `{total, items: [{orgnr, name, address: {lines, postal_code, city, municipality}, employees?, started?}]}` |
| `job_postings` | List of `{title, url, published?, expires?, location?, source, employer?, employer_orgnr?, relation?}` |
| `hiring_signal` | `{hiring: true/false, open_postings}`; `text` is usually set |
| `public_activity` | List, newest first: `{date, kind, title, url?, source}`. `kind` is one of `registry_change`, `leadership_update`, `name_change`, `job_posted`, `website_news` |
| `registry_change_events` | List of `{date, type, changes: [text]}` |
| `role_change_events` | List of `{date, type}` |
| `website_news` | List of `{date, title, url}` |
| `social_profiles` | List of `{platform: linkedin/facebook/instagram/x/youtube/tiktok, url}` |
| `former_names` | List of `{name, from, to}` |
| `industry` | Usually in `text`; the value is a list of `{code, description, rank}` |
| `business_description` | Usually in `text`; the value is `{activity, statutory_purpose}` |
| `website_locations` | List of address objects (`streetAddress`, `postalCode`, `addressLocality`, …) |
| `website_contact` | `{emails: [...], phones: [...]}` |
| `revenue_change` | Usually in `text`, for example `"+191.6% (2024→2025)"` |

## Change item

`{type, field, before, after, material: true/false}`. Example types: `new_role`, `removed_role`, `new_job`, `closed_job`, `new_filing`, `changed_address`, `changed_name`, `changed_status`, `new_location`, `availability_changed`.

## Rules the site must follow

- Show `reason` for every fact that is not `available`. Never show a missing value as 0 or as a blank.
- Never change, round or estimate numbers, apart from display formatting ("NOK 5.3 m").
- Every available fact gets a "Source" button that opens its evidence (`ev` → `E`).
- For `form = "ENK"`, hide `registered_contact` and `website_contact` (personal data).
