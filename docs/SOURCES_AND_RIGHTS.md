# Sources, rights, outbound URL policy and secrets

## Sources used

| Source | What we read | Access | Licence and terms | Source class |
|---|---|---|---|---|
| Brønnøysundregistrene – Enhetsregisteret, `data.brreg.no/enhetsregisteret/api` | Entities (batched by org number), roles, establishments, the entity change feed (`oppdateringer/enheter`) and the role change feed (`oppdateringer/roller`) | Open API, no key, about 8 req/s | NLOD 2.0 | `official_registry` |
| Brønnøysundregistrene – Regnskapsregisteret, `data.brreg.no/regnskapsregisteret/regnskap/{orgnr}` | Filed annual accounts: every period the open API returns | Open API, no key | NLOD 2.0 | `official_accounts` |
| NAV job vacancy feed, `pam-stilling-feed.nav.no` | Feed headers for the recent window; ad details only for name-matched candidates, published only when the ad's `employer.orgnr` matches | Official API with bearer token: `NAV_FEED_TOKEN` if set, otherwise NAV's published public token from `/api/publicToken` | [NAV API terms](https://arbeidsplassen.nav.no/vilkar-api). Inactive ads are dropped; contact persons are never stored. | `public_job_feed` |
| The company's own website | Homepage plus up to 2 contact/about/privacy pages (identity proof), the careers page and up to 4 of its job pages, the RSS feed or news page and up to 3 of its articles | Direct HTTP GET, honest user agent, robots.txt honoured, 1 req/s per host, ≤ 20 requests per company | Company-published pages. Facts are cited with short spans, not republished. | `company_owned` |
| Recruitment pages linked from the verified website (Teamtailor, Jobylon, Webcruiter, etc.) | The page the company links to | Same rules as above, with robots.txt checked for that host | Company-published job listings | `company_linked` |

## Not used (on purpose)

- **Platforms with restrictive terms.** No search-result scraping, and no LinkedIn, Meta, Glassdoor, Indeed or Google clients. Social profiles are reported only as outbound links found on the verified site; their contents are not fetched.
- **Paid or licensed feeds and LLMs.** None are used, so the declared third-party cost is $0.

## Outbound URL policy

- **Fixed API hosts.** The only API hosts are `data.brreg.no` and `pam-stilling-feed.nav.no`.
- **Website hosts and how they are chosen.** They come only from:
  1. the register's `hjemmeside`;
  2. the employer homepage on a NAV ad already tied to the org number;
  3. the domain of the registered company email, if it is not a free-mail provider;
  4. up to 4 domains spelled like the legal name or its distinctive words (`.no`, then `.com`), used only when 1–3 give nothing and only when the domain resolves.
- **Links followed.** Only same-site links (contact, careers, news, and up to 3 articles on the site's own news list), recognised recruitment-platform hosts linked from the verified site, and RSS/Atom links in the site's own `<head>`. When the static menu links no careers or news page, a few common paths on the verified site are tried (`/karriere`, `/ledige-stillinger`, `/jobb`; `/nyheter`, `/aktuelt`, `/news`, `/blogg`); a page counts only if it really exists there and calls itself that section.
- **robots.txt.** It is fetched and honoured for every host before any page:
  - 401/403 counts as a block;
  - 5xx or unreachable means the site is not crawled and the fetch is reported as failed;
  - 404 means no restrictions.
- **Request settings.** Redirects are followed for at most 5 hops, each hop is counted, and the full chain is stored as evidence. The timeout is 10 s per website request, a body is read up to 1.5 MB, and only static HTML is read (no JavaScript execution).
- **Proxies.** Standard proxy environment variables (`HTTPS_PROXY`) are respected.

## Secrets

- **None are required.**
- **Optional `NAV_FEED_TOKEN`.** A registered consumer token for the NAV feed, read only from the environment and never logged or written to outputs. Without it, the public token NAV publishes for open use is fetched at run time; no token is stored in the repository.
- **No other credentials.** None are read, and none tied to a personal account.

## Personal data

- **Role holders.** The register publishes role holders' names; they are shown in the company context only.
- **Birth dates.** Present in the roles API, but never copied: evidence spans point at the name and role-type paths only.
- **Job-ad contact persons.** Never stored.
- **Sole proprietorships (ENK).** The register's phone and email may belong to a private person. They are shown only as the register publishes them.

## Licences of included code

- `scripts/select_entry_batch.py` is copied unchanged from the Builderr Signalpost starter kit (MIT License, © 2026 builderr).
- `norway_agent/assets/public-sans-*.woff2` is Public Sans (© The Public Sans Project Authors), used under the SIL Open Font License 1.1 (`norway_agent/assets/PUBLIC_SANS_OFL.txt`). It is embedded in the offline viewer.
- All other code belongs to this repository.
