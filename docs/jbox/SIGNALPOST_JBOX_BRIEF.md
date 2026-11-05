Build "Signalpost", a public website for checking Norwegian companies. It has three jobs, and every screen must make them obvious and fast on a laptop and on a phone:

1. FIND a company (by name, org number, city or industry).
2. COMPARE two companies side by side.
3. CHECK WHERE A FACT CAME FROM. Every fact has a "Source" button that shows the original source link, when it was retrieved, and the exact quoted text.

The site only displays data produced by an external research agent. It never edits, estimates or recalculates numbers. It has no AI features, no login and no forms that change data.

DATA (read DATA_SPEC.md and sample-profiles.jsonl, both attached)
- Fetch the data server-side from env var DATA_URL, default https://raw.githubusercontent.com/ritambra08/Norway_Company_Agent/main/submission/profiles.jsonl. Revalidate every 60 minutes. No database is needed.
- The file is JSON Lines: one company record per line (about 100 lines, 3 MB). Skip empty or unparsable lines. If a fetch fails, keep serving the last good copy and show a small "data temporarily unavailable" note only if there is no copy at all.
- From the records, build a light directory index on the server (org, name, brand, form, city, industry, registry_status, revenue, net_result, currency, year, employees, website, jobs, completeness, sections, run). Send only the index to the home page; send one full record per company page.
- Each record has "facts" (keyed by field name, each with label, state, text, optional value / ev / note / stale / reason), "E" (the evidence list that ev indexes point into), "sections", "summary", "changes" and "errors". DATA_SPEC.md describes every field and value shape.
- The six states and their plain-English labels: available = "Found", not_available = "Not found", ambiguous = "Unclear match", blocked = "Blocked by the site", failed = "Couldn't check", not_applicable = "Doesn't apply".

PAGE 1 – HOME "/" (the FIND task)
- Hero: "Check any Norwegian company. Every fact shows its source." Below it, one large search box with instant suggestions as you type (top 8 matches on name, brand, org number with or without spaces, city or industry). Arrow keys and Enter work; on phones it is full width with a large tap target.
- Under the hero, three small cards that explain the site: Find · Compare · Check the source.
- A stats strip: number of companies, "Data updated <date from the newest record's run id>", and "Every fact links to its source".
- A directory below:
  - Filter chips: Has accounts, Verified website, Hiring, Not active, Changed since last run.
  - Sort: Most data found (default, by completeness), Name A–Z, Revenue, Employees.
  - Each row shows name, org number, city, industry, a revenue badge ("NOK 5.3 m · 2025"), "Website ✓" when verified, a red badge when registry_status is not "active", a thin "data found" bar, and an "Add to compare" button.
- A sticky bottom compare tray appears after the first pick: "Compare (1/2)", then a "Compare now" button at 2. The tray stays visible across pages.

PAGE 2 – COMPANY "/company/[org]" (the CHECK task)
- Header: name, brand (if different), org number with a copy button, legal form in plain English (AS = private limited company, ASA = public limited company, ENK = sole proprietorship, NUF = Norwegian branch of a foreign company, DA/ANS = partnership, SA = cooperative, STI = foundation, BRL = housing cooperative, ESEK = property co-ownership, FLI = association, SAM = co-ownership), city, and a status pill (Active / Bankrupt / In liquidation …). Buttons: "Add to compare" and "Share" (copies the page link).
- Key-figures row of cards: Revenue (with year and the change vs the previous year from revenue_change, shown with an up or down arrow), Net result, Employees, Website (verified ✓ or not found), Open jobs. Each card has a small "Source" icon that opens the evidence drawer for that fact.
- Revenue history: a small bar chart from facts.annual_accounts_history.value (revenue per period_end year), shown only when there are 2 or more periods, captioned "Source: Regnskapsregisteret (filed annual accounts)". Each bar's tooltip shows the exact amount.
- The summary text in a card. Then quick-question chips: "What does it do?", "Who runs it?", "Where does it operate?", "Latest filing?", "Is it hiring?", "What changed?". Each one scrolls to and briefly highlights the matching section.
- "What changed since last run": the changes list (type, field, before → after, material in bold). If the list is empty, show "No changes since the last check".
- Seven sections, in this order, each with a state badge from sections. Use accordions on phones, all open on desktop. List these facts, skipping any the record lacks:
  1. Company brief: legal_name, public_brand_name, organisation_form, registry_status, industry, business_description, website_description, registered_employees, founding_date, registration_date, vat_registered, business_register, share_capital, group_membership, former_names, institutional_sector, parent_unit
  2. Latest financials: annual_accounts_latest, revenue, operating_result, profit_before_tax, net_result, equity, total_assets, total_liabilities, personnel_costs, revenue_change, annual_accounts_history, latest_filed_accounts_year
  3. Who runs it: chief_executive, board_chair, board_members, auditor, accountant, leadership
  4. Where it operates: business_address, postal_address, registered_workplaces, website_locations, registered_contact, website_contact
  5. Working here: hiring_signal, job_postings, careers_page
  6. Online presence: official_website, registered_website, social_profiles
  7. Recent activity: public_activity, registry_change_events, role_change_events, website_news
- Showing an available fact: show "text". If text is empty, render "value" the way DATA_SPEC.md describes:
  - lists become tidy rows with the date in bold, the title or name as a link when a url exists, and other short fields in grey;
  - an annual_accounts_latest value becomes a small table of its figures;
  - registered_workplaces shows the total and then each workplace with its address;
  - public_activity becomes a dated timeline with an icon per kind.
  Show the note in small grey text, and a "kept from previous run" tag if stale is true.
- Showing any other state: show its plain-English label in grey with the reason, for example "Not found – No website is declared in Enhetsregisteret." Never show a missing value as 0 or as a blank. Show 0 only when the value really is 0.
- THE SOURCE DRAWER (most important): every available fact has a "Source" button. It opens a right-side panel on desktop, or a bottom sheet on phones, listing E[i] for each index i in that fact's ev:
  - source name;
  - the source URL as an "Open original ↗" link (new tab);
  - "Retrieved 5 Nov 2026, 15:39";
  - the quoted text x in a quote block;
  - the location l in small monospace;
  - the hash prefix h labelled "content fingerprint";
  - a "from previous run" tag when p = 1.
  Esc or swiping down closes it. Focus goes into the drawer and back to the button afterwards.
- Privacy: if form is "ENK", hide registered_contact and website_contact and show "Hidden: personal data of a sole proprietor".
- At the bottom: a collapsible "Run notes" list from errors.

PAGE 3 – COMPARE "/compare?a=ORG&b=ORG" (the COMPARE task)
- Two company pickers at the top, each with the same instant search, plus a "Swap" button. The URL updates, so a comparison can be shared.
- Rows: org number, legal form, city, industry, register status, revenue with year, revenue change, net result, equity, employees, verified website, open jobs, CEO, board chair, number of registered workplaces, latest activity date, data found %, and each of the 7 section states.
- Every cell that holds a fact has a small Source icon that opens the same drawer.
- Missing values show "Not found" with the reason in a tooltip, never 0.
- On phones, the two company names stay sticky at the top and the rows scroll underneath.
- Below the table: both companies' revenue history in one small grouped bar chart, only when both have 2 or more periods.

PAGE 4 – "/how-it-works"
In plain words:
- the data comes from Brønnøysundregistrene (Enhetsregisteret and Regnskapsregisteret, NLOD 2.0), the NAV job vacancy feed, and companies' own websites, verified by organisation number;
- facts are published only when tied to the exact organisation number;
- financial figures are copied unchanged from filed accounts;
- missing data is labelled with its reason, never shown as zero;
- every fact links to its source.

Include a small table explaining the six states.

DESIGN SYSTEM: follow the attached target-*.png screenshots closely. They are the exact design to reproduce, on desktop and phone.
- Typeface: Public Sans (Google Fonts) at weights 400, 600 and 800, for everything. Use tabular numerals for figures. Headlines are 800 weight with slightly tight letter-spacing, and text-wrap: balance.
- Colours (light mode):
  - paper #F5F7FA (page background), surface #FFFFFF, ink #14233A (text), muted #55667C, faint #8494A8, line #DCE3EC, mist #EAF0F7;
  - fjord blue #1F5FA8 for links, buttons and focus;
  - pine #2E7D5B for "Found" and filed accounts, amber #9A5A00 for "Unclear match" and "Blocked", red #B3261E for bankrupt;
  - green-tinted quote background #E5F3EC.
  Dark mode: paper #0D1625, surface #131F32, ink #E7EDF5, muted #A3B2C5, line #25344A, fjord #86B5F2, pine #72C99F.
- Signature element, "source marks": a small pill-shaped button with a 1.5 px outline in its colour and 2–3 letters:
  - ER = Enhetsregisteret, fjord blue;
  - RR = Regnskapsregisteret, pine green;
  - NAV = the NAV job feed, amber;
  - WEB = the company's own website, violet #6E5BB0.

  Pick the mark from each evidence item's source name s. Put the marks at the right end of every fact row, every key-figure cell and every compare cell. Selecting a mark opens the source extract. The marks are the visual identity of the site, so keep everything around them quiet.
- Home hero: on the left, the headline "Check a Norwegian company, fact by fact." (about 58 px on desktop), the lead text, and a large search box (58 px tall, soft shadow). On the right, a "specimen" card for the company with the most data found:
  - three rows (Revenue, Chief executive, Business address), each with its source mark;
  - below them, an open extract showing the revenue evidence: "Operating revenue in the filed accounts, Regnskapsregisteret", the exact value (for example "NOK 540 691"), and the retrieved date;
  - caption: "Every fact has a source mark. Select one to see the original."
  One subtle unfold animation on page load is the only decorative motion; respect prefers-reduced-motion.
- Directory: one bordered list with thin row dividers, not separate cards. Desktop columns: Company (name, with the org number and badges underneath) | City | Industry | Revenue (right-aligned, year underneath) | Data found (thin bar and %) | a Compare toggle button. On phones each row shows the name, org number, badges and revenue, with Compare on the right.
- Compare tray: a dark ink pill fixed at the bottom centre showing the picked names (each with ×) and a blue "Compare now" button.
- Company page:
  - a 64 px rounded-square monogram tile (two initials, ink background, white letters) beside the big company name, with a line underneath: "Org. nr 827340162 Copy", the legal form in plain English, the city and a status pill;
  - "Add to compare" (blue) and "Copy link" buttons on the right;
  - the key figures as ONE bordered strip divided into 5 cells: Revenue (with ▲/▼ change), Net result, Employees, Website, Chief executive. On phones it becomes a 2-column grid;
  - then the summary card, the revenue bar chart, and the 7 sections as label | value | marks rows;
  - a sticky right column, "Jump to an answer", holding the question buttons and a small "Not found for this company" box. On phones it becomes a horizontally scrolling row of buttons above the sections.
- Source extract: a 460 px right-side panel on desktop, or a bottom sheet with a grab handle on phones. The title is "Source extract", with the fact label, company and value underneath. Then one block per evidence item:
  - order: the actual value first; currency, accounting period and accounts type last;
  - heading: what the item is (Operating revenue, Currency, Accounting period …; see DATA_SPEC.md);
  - the value in the green-tinted quote block, shown readably (numbers as "540 691", periods as "2025-01-01 to 2025-12-31", JSON strings without quotes);
  - then Source, Retrieved ("5 Nov 2026 at 15:44 UTC"), Found at, and Fingerprint (sha256 prefix);
  - finally the "Open the original source" link with an external-link icon.
- Compare page:
  - the title "Compare two companies", two search pickers and a round swap button;
  - a table with grouped rows (Identity, Money, People and places, Online and activity) and a sticky row of company names;
  - the higher revenue, net result and equity shown in bold, and source marks in each cell;
  - a grouped revenue bar chart below.
- Copy: sentence case everywhere, with no all-caps labels. Use plain English. State labels are Found / Not found / Unclear match / Blocked by the site / Couldn't check / Doesn't apply, always followed by the reason.
- Quality:
  - mobile-first (test at 360 px), tap targets of at least 44 px, no horizontal scrolling;
  - visible focus rings, keyboard navigation, Esc closes the drawer, AA contrast;
  - skeleton loaders while data loads, and no layout jumps;
  - money formatted as "NOK 15.0 m" / "NOK 1.2 bn" / "NOK 850 k" / "NOK −2.2 m", and dates as "5 Nov 2026";
  - a favicon and Open Graph tags on every page;
  - this footer on every page: "Data: Brønnøysundregistrene (NLOD 2.0), NAV job vacancy feed, companies' own websites. Every fact links to its source. Built with JBOX."
