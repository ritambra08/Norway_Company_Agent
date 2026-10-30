# Building the Signalpost app with JBOX

The crawler in this repository is deterministic Python. It produces the scored envelopes and the evidence. The **user-facing product** (find, compare, verify, on desktop and mobile) is built with [JBOX](https://jboxai.com), which generates a hosted Next.js + Supabase app from a brief.

`out/index.html` is the zero-dependency fallback viewer. The JBOX app is the hosted product for the gallery and for real users.

## 1. Data the app imports

**Source file.** Use `out/profiles.jsonl`, which holds one JSON object ("card") per company, written by every run.

**Card fields:**

| Field | Contents |
|---|---|
| `org` | 9-digit organisation number |
| `name`, `brand` | Legal name; public brand name (may be null) |
| `form`, `city`, `industry` | Legal form, city and industry |
| `status` | Profile status (`available`, `not_available`, …) |
| `registry_status` | `active`, `bankrupt`, … |
| `revenue`, `net_result`, `currency`, `year` | Latest accounts |
| `employees`, `website`, `jobs` | Employee count, verified website, number of open postings |
| `completeness` | 0–1: share of key fields found |
| `summary` | Plain-language profile |
| `sections` | Map from the 7 profile sections to an availability state |
| `facts` | Map from field to `{label, state, text, value?, reason?, note?, stale?, ev:[indexes into E]}` |
| `E` | Evidence list: `{s: source name, u: source URL, t: retrieved at, h: sha256 prefix, x: quoted span, l: locator, p: from previous run}` |
| `changes` | Typed changes since the previous run (`type`, `field`, `before`, `after`, `material`) |
| `errors` | Run notes |

## 2. Paste this brief into JBOX

> Build **Signalpost**, a research app for Norwegian companies. Users decide whether to apply to, sell to, partner with or invest in a company, so every fact must show where it came from.
>
> **Data.** Use a Supabase table `companies` with columns: `org` (text, primary key), `name`, `brand`, `city`, `industry`, `form`, `status`, `registry_status`, `revenue` (numeric), `net_result` (numeric), `year`, `employees` (int), `website`, `jobs` (int), `completeness` (numeric), `summary` (text), `card` (jsonb: the whole card), `run_id`, `updated_at`. Add a table `runs` (`run_id`, `imported_at`, `count`).
>
> **Admin import.** Admin-only page, behind auth. The admin uploads a `profiles.jsonl` file (one JSON object per line). Upsert each line into `companies` by `org`, record the run, and show the counts imported.
>
> **Directory (public).**
> - Search by name, org number, city or industry.
> - Filter chips: Verified website, Hiring, Has accounts, Changed since last run (any `card.changes[].material`), Not active (`registry_status` ≠ `active`).
> - Sort: Most data found (`completeness`), Name, Revenue, Employees, Open jobs.
> - Each row shows the name, org number, city, industry, a revenue badge, a "website ✓" badge, a jobs badge, a "changed" badge and a completeness meter.
>
> **Company page (public).**
> - **Header:** name, org number, legal form, city and profile status.
> - **Summary:** the plain-language text from `card.summary`.
> - **"What changed since last run":** from `card.changes`, with `before` → `after`.
> - **Seven sections**, in this order: Company brief, Latest financials, Who runs it, Where it operates, Working here, Online presence, Recent activity. Each section has a state badge from `card.sections`.
> - **Rows:** each row is a fact from `card.facts`. Show the label and `text`; if `text` is empty, render `value` (lists as bullet lines, objects as key: value).
> - **Missing facts:** when `state` is not `available`, show the state in grey and the `reason`. Never show a missing value as 0.
> - **Evidence drawer:** every available fact has a "Source" button that opens a drawer listing its evidence items (`card.E[i]` for each index in `ev`): source name, clickable URL, retrieval time, hash prefix, the quoted span and the locator. Items from a previous run get a "previous run" tag. Stale facts get a "kept from previous run" tag.
>
> **Compare.** Pick up to 3 companies from the directory. A side-by-side table shows org number, legal form, city, industry, register status, revenue (with year), net result, employees, website, open jobs and data completeness.
>
> **Design.** Clean and mobile-first. On phones the directory and company page are separate screens with a back button. Use system fonts and dark mode. Make it accessible: labelled controls and keyboard-navigable drawers. Show a footer on every page: "Data: Brønnøysundregistrene (NLOD 2.0), NAV job feed, companies' own websites. Every fact links to its source."
>
> **Roles.** Public visitors are read-only. An admin can import runs.

## 3. Wire it up

1. Run the agent, for example `make smoke` or the official batch command. This writes `out/profiles.jsonl`.
2. In the JBOX app, open **Admin → Import** and upload `profiles.jsonl`.
3. Publish the JBOX app and put its URL in `SUBMISSION.md` and in the submission email ("UI built with JBOX: <url>").

## 4. Eligibility note

The challenge page offers a JBOX bonus "for qualifying agents built with JBOX", but doesn't define what counts. Before relying on it, ask Builderr (submit@builderr.ai) to confirm that a JBOX-built user app on top of a deterministic Python crawler qualifies.
