from __future__ import annotations

import gzip
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from norway_agent import web
from norway_agent.config import Config
from norway_agent.inputs import read_batch
from norway_agent.names import domain_candidates, match_key, page_names_company
from norway_agent.pipeline import run
from norway_agent.validate import validate
from tests import fixtures as fx


def fake_resolves(host, timeout=3.0):
    return host in ("nordlysholding.no", "www.nordlysholding.no", "glimtanalyse.com")


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += max(0.0, s)


def do_run(tmp: Path, net=None, cfg=None, previous=None, run_id="t1"):
    net = net or fx.FakeInternet()
    batch = tmp / "batch.jsonl"
    batch.write_text("\n".join(json.dumps(x) for x in fx.batch_lines()) + "\n", encoding="utf-8")
    clock = Clock()
    with mock.patch.object(web, "_resolves", fake_resolves):
        rep = run(str(batch), tmp / "out" / "envelopes.jsonl", tmp / "out" / "run-report.json", run_id,
                  cfg or Config(), previous=previous, opener=net, clock=clock, sleep=clock.sleep,
                  expected_count=14)
    envs = {e["organisation_number"]: e for e in map(json.loads, (tmp / "out" / "envelopes.jsonl").read_text().splitlines())}
    return rep, envs, net


def claim(env, field):
    return next(c for c in env["claims"] if c["field"] == field)


class ContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.rep, cls.envs, cls.net = do_run(cls.tmp)

    def test_one_terminal_envelope_per_input(self):
        self.assertEqual(len(self.envs), 14)  # 13 + invalid; duplicate dropped
        self.assertTrue(self.rep["one_terminal_envelope_per_input"])
        self.assertEqual(self.rep["input"]["duplicates_dropped"], 1)
        for e in self.envs.values():
            self.assertIn(e["status"], ("available", "not_available", "blocked", "not_applicable", "ambiguous", "failed"))
            for k in ("run", "claims", "evidence", "changes", "errors", "operations"):
                self.assertIn(k, e)
            self.assertEqual(e["operations"]["third_party_cost_usd"], 0)

    def test_output_lines_survive_unicode_line_separators(self):
        # The Fjordtech page carries U+2028 and U+0085 (as real sites do); evidence spans copy them.
        out = self.tmp / "out"
        for name in ("envelopes.jsonl", "profiles.jsonl"):
            raw = (out / name).read_text(encoding="utf-8")
            for ch in (" ", " ", "\x85"):
                self.assertNotIn(ch, raw, f"raw line separator in {name}")
            self.assertEqual(len(raw.splitlines()), 14)
        spans = json.dumps([e.get("claim_span") for e in self.envs[fx.A]["evidence"]], ensure_ascii=False)
        self.assertIn(" ", spans)  # the character itself is preserved, only escaped on disk
        n, problems, _ = validate(out / "envelopes.jsonl")
        self.assertEqual((n, problems), (14, []))

    def test_invalid_and_unknown_numbers_are_explicit(self):
        self.assertEqual(self.envs["123456789"]["status"], "not_available")
        self.assertIn("not a valid", self.envs["123456789"]["errors"][0]["message"])
        self.assertEqual(self.envs[fx.NF]["status"], "not_available")

    def test_every_non_available_claim_has_a_reason(self):
        for e in self.envs.values():
            for c in e["claims"]:
                if c["availability"] != "available":
                    self.assertTrue(c.get("reason"), (e["organisation_number"], c["field"]))
                else:
                    self.assertTrue(c["evidence_ids"], (e["organisation_number"], c["field"]))

    def test_validate_replays_every_span(self):
        n, problems, stats = validate(self.tmp / "out" / "envelopes.jsonl", str(self.tmp / "batch.jsonl"))
        self.assertEqual(problems, [])
        self.assertGreater(stats["spans_replayed"], 100)

    def test_financials_verbatim_with_history_and_change(self):
        a = self.envs[fx.A]
        rev = claim(a, "revenue")
        self.assertEqual(rev["value"], {"amount": 15000000.0, "currency": "NOK", "period_start": "2025-01-01",
                                        "period_end": "2025-12-31", "accounts_type": "SELSKAP"})
        self.assertEqual(rev["reporting_period"], {"start": "2025-01-01", "end": "2025-12-31"})
        hist = claim(a, "annual_accounts_history")["value"]
        self.assertEqual([h["period_end"] for h in hist], ["2025-12-31", "2024-12-31", "2023-12-31"])
        ch = claim(a, "revenue_change")["value"]
        self.assertEqual(ch["change_pct"], 25.0)
        self.assertTrue(claim(a, "revenue_change")["derived_from"])

    def test_accounts_refusal_and_missing_are_not_zero(self):
        g = self.envs[fx.G]
        self.assertEqual(claim(g, "revenue")["availability"], "not_available")
        self.assertIn("cannot serve", claim(g, "revenue")["reason"])
        self.assertIsNone(claim(g, "revenue")["value"])
        e = self.envs[fx.E]
        self.assertEqual(claim(e, "revenue")["availability"], "not_available")
        self.assertEqual(claim(e, "registered_employees")["value"], {"range": "0-4"})

    def test_people_current_only_and_no_birth_dates(self):
        a = self.envs[fx.A]
        names = [r["name"] for r in claim(a, "leadership")["value"]]
        self.assertIn("Ola Hansen", names)
        self.assertNotIn("Gammel Medlem", names)
        self.assertEqual(claim(a, "chief_executive")["value"]["name"], "Ola Hansen")
        self.assertEqual(claim(a, "auditor")["value"]["holder_type"], "organisation")
        blob = json.dumps(self.envs, ensure_ascii=False)
        self.assertNotIn("1975-05-17", blob)
        self.assertNotIn("fodselsdato", blob)
        self.assertNotIn("Hemmelig Kontakt", blob)  # NAV contact persons never copied
        self.assertEqual(claim(self.envs[fx.E], "chief_executive")["value"]["role_code"], "INNH")

    def test_workplaces_do_not_leak_foreign_establishments(self):
        wp = claim(self.envs[fx.A], "registered_workplaces")["value"]
        self.assertEqual({i["orgnr"] for i in wp["items"]}, {fx.SUB_A1, fx.SUB_A2})

    def test_website_verified_by_org_number(self):
        a = self.envs[fx.A]
        w = claim(a, "official_website")
        self.assertEqual(w["availability"], "available")
        self.assertEqual(w["value"]["verification"], "org_number_on_site")
        self.assertEqual(w["value"]["url"], "https://www.fjordtech.example/")
        ev = {e["id"]: e for e in a["evidence"]}
        self.assertTrue(any(fx.A[:3] in ev[i]["claim_span"] for i in w["evidence_ids"]))
        self.assertTrue(any("redirect_chain" in a["sources"][ev[i]["source_ref"]] for i in w["evidence_ids"]))
        rec = [ev[i] for i in w["evidence_ids"] if ev[i]["source_class"] == "official_registry"][0]
        self.assertEqual(rec["source_url"], f"https://data.brreg.no/enhetsregisteret/api/enheter/{fx.A}")

    def test_profiles_exclude_share_links(self):
        prof = claim(self.envs[fx.A], "social_profiles")["value"]
        urls = {p["url"] for p in prof}
        self.assertIn("https://www.linkedin.com/company/fjordtech", urls)
        self.assertIn("https://www.facebook.com/fjordtech", urls)
        self.assertIn("https://www.youtube.com/@fjordtech", urls)
        self.assertFalse(any("sharer" in u or "intent" in u for u in urls))
        self.assertFalse(any(u.endswith("/wix") or "webbyraaet" in u for u in urls))  # builder / agency links
        emails = claim(self.envs[fx.A], "website_contact")["value"]["emails"]
        self.assertIn("post@fjordtech.example", emails)
        self.assertNotIn("hei@webbyraaet.example", emails)  # the web agency's address is not the company's

    def test_parent_site_is_ambiguous_not_merged(self):
        c = self.envs[fx.C]
        w = claim(c, "official_website")
        self.assertEqual(w["availability"], "ambiguous")
        self.assertIn("different organisation number", w["reason"])
        self.assertIsNone(w["value"])  # the parent's site is not named as a value
        self.assertNotIn("bergenbygg", json.dumps(c).lower().replace("-", ""))  # nor anywhere in the envelope
        self.assertNotEqual(claim(c, "registered_website")["availability"], "available")
        self.assertTrue(c["abstentions"])

    def test_guessed_namesake_site_is_discarded(self):
        b = self.envs[fx.B]
        self.assertEqual(claim(b, "official_website")["availability"], "not_available")
        self.assertNotIn("nordlysholding", json.dumps(claim(b, "social_profiles")))
        # The rejected guess is not named anywhere: not in a reason, a checked source or the summary.
        self.assertNotIn("nordlysholding", json.dumps(b).lower())
        self.assertIn("domain spelled like the legal name", claim(b, "official_website")["reason"])

    def test_email_domain_of_employer_is_never_published(self):
        h = self.envs[fx.H]
        self.assertEqual(claim(h, "official_website")["availability"], "not_available")
        self.assertNotIn("formuex", json.dumps([c for c in h["claims"] if c["availability"] == "available"
                                                and c["field"] != "registered_contact"]))
        self.assertNotEqual(claim(h, "job_postings")["availability"], "available")

    def test_email_domain_of_manager_listing_the_company_is_never_published(self):
        i = self.envs[fx.I]
        self.assertEqual(claim(i, "official_website")["availability"], "not_available")
        self.assertNotIn("obblx", json.dumps([c for c in i["claims"] if c["availability"] == "available"
                                              and c["field"] != "registered_contact"]))

    def test_email_domain_named_after_company_is_verified(self):
        w = claim(self.envs[fx.J], "official_website")
        self.assertEqual(w["availability"], "available")
        self.assertIn("domain_matches_legal_name", w["value"]["verification"])

    def test_com_guess_after_failed_email_domain_verified_by_org_number_on_unlinked_contact_page(self):
        w = claim(self.envs[fx.M], "official_website")
        self.assertEqual(w["availability"], "available")
        self.assertEqual(w["value"]["verification"], "org_number_on_site")
        self.assertEqual(w["value"]["candidate_from"], "name_guess")
        self.assertIn("glimtanalyse.com", w["value"]["url"])
        self.assertNotIn("regnskapx", json.dumps(w))

    def test_primary_evidence_comes_first(self):
        a = self.envs[fx.A]
        ev = {e["id"]: e for e in a["evidence"]}
        first = ev[claim(a, "revenue")["evidence_ids"][0]]
        self.assertTrue(first["locator"]["path"].endswith("sumDriftsinntekter"))
        self.assertEqual(first["claim_span"], "15000000.0")

    def test_parked_domain_is_never_published(self):
        self.assertEqual(claim(self.envs[fx.L], "official_website")["availability"], "not_available")

    def test_job_ad_under_longer_business_name_found_by_org_number(self):
        jobs = claim(self.envs[fx.K], "job_postings")
        self.assertEqual(jobs["availability"], "available")
        self.assertEqual(jobs["value"][0]["title"], "Systemutvikler")

    def test_robots_block_is_reported(self):
        self.assertEqual(claim(self.envs[fx.D], "official_website")["availability"], "blocked")
        self.assertNotIn("https://robotstopp.example/", [u for u in self.net.calls if not u.endswith("robots.txt")])

    def test_jobs_tied_by_org_number(self):
        a = self.envs[fx.A]
        jobs = claim(a, "job_postings")["value"]
        titles = {j["title"] for j in jobs}
        self.assertIn("Senior utvikler", titles)               # NAV, employer = company
        self.assertIn("Konsulent Oslo", titles)                # NAV, employer = registered establishment
        self.assertNotIn("Snekker", titles)                    # same name, different org number
        self.assertIn("Backend-utvikler", titles)              # JSON-LD JobPosting on careers page
        self.assertIn("Product Designer", titles)              # ATS link on careers page
        self.assertNotIn("Lagerarbeider (vikar)", titles)      # posting for another employer
        for junk in ("Hopp til innhold", "Se våre ledige stillinger", "Slik er det å jobbe i Fjordtech"):
            self.assertNotIn(junk, titles)                     # navigation / text about working there
        rel = {j["title"]: j.get("relation") for j in jobs}
        self.assertEqual(rel["Konsulent Oslo"], "establishment")
        self.assertTrue(claim(a, "hiring_signal")["value"]["hiring"])

    def test_checked_zero_jobs_differs_from_not_checked(self):
        b = claim(self.envs[fx.B], "job_postings")
        self.assertEqual(b["availability"], "not_available")
        self.assertEqual(b["checked_sources"][0]["status"], "checked")

    def test_activity_is_dated_and_merged(self):
        a = self.envs[fx.A]
        acts = claim(a, "public_activity")["value"]
        kinds = {x["kind"] for x in acts}
        self.assertTrue({"registry_change", "leadership_update", "website_news", "job_posted", "name_change"} <= kinds)
        dates = [x["date"] for x in acts]
        self.assertEqual(dates, sorted(dates, reverse=True))
        news = [n["title"] for n in claim(a, "website_news")["value"]]
        self.assertIn("Fjordtech vinner kontrakt med Havbruk Nord", news)
        self.assertNotIn("Gammel nyhet", news)
        ev = claim(a, "registry_change_events")["value"]
        emp = [e for e in ev if any("employee count" in c for c in e["changes"])]
        self.assertEqual(len(emp), 1)  # monthly headcount refreshes collapsed
        self.assertTrue(any("annual accounts for 2025 registered" in c for e in ev for c in e["changes"]))

    def test_summary_is_grounded(self):
        s = self.envs[fx.A]["summary"]
        self.assertIn("FJORDTECH AS", s["text"])
        self.assertIn("NOK 15.0 million", s["text"])
        self.assertIn("Hiring", s["text"])
        self.assertIn("First profile", s["what_changed"])
        self.assertIn("legal_name", s["based_on_fields"])

    def test_more_financial_lines_copied_never_invented(self):
        a = self.envs[fx.A]
        self.assertEqual(claim(a, "sales_revenue")["value"]["amount"], 14999000.0)
        self.assertEqual(claim(a, "tax_expense")["value"]["amount"], 240000.0)
        self.assertEqual(claim(a, "net_financial_items")["value"]["amount"], 0.0)  # a reported zero is a value
        self.assertEqual(claim(a, "financial_income")["availability"], "not_available")  # empty: never zero
        hist = claim(a, "annual_accounts_history")["value"]
        self.assertTrue(all("sales_revenue" in row for row in hist))
        self.assertTrue(all("financial_income" not in row for row in hist))

    def test_register_dates_are_dated_public_activity(self):
        pa = claim(self.envs[fx.A], "public_activity")
        kinds = {x["kind"] for x in pa["value"]}
        self.assertTrue({"founded", "registered", "vat_registered", "establishment_opened"} <= kinds)
        self.assertIn("Establishment FJORDTECH AVD OSLO started in Oslo", [x["title"] for x in pa["value"]])
        e = claim(self.envs[fx.E], "public_activity")  # a sole proprietorship with no changes still has history
        self.assertEqual(e["availability"], "available")

    def test_summary_cites_its_sources(self):
        s = self.envs[fx.A]["summary"]
        ev = {e["id"]: e for e in self.envs[fx.A]["evidence"]}
        nums = {x["n"]: x for x in s["sources"]}
        self.assertTrue(nums and all(x["url"] and x["retrieved_at"] for x in nums.values()))
        for st in s["statements"]:
            for i in st["evidence_ids"]:
                self.assertIn(i, ev)
            for n in st["sources"]:
                self.assertIn(n, nums)
        first = s["statements"][0]
        self.assertEqual(first["section"], "what_it_does")
        self.assertTrue(first["sources"])
        self.assertIn(f"[{first['sources'][0]}", s["text"].splitlines()[0])
        self.assertTrue(nums[first["sources"][0]]["url"].startswith("https://data.brreg.no/"))
        self.assertNotIn("[", s["plain_text"].splitlines()[0])  # the display text has no markers
        # what changed: the dated name change from the register, with its source
        self.assertIn("changed its registered name from FJORD SOFTWARE AS to FJORDTECH AS", s["what_changed"])
        b = self.envs[fx.B]["summary"]
        self.assertIn("official website (no website could be tied", b["text"])

    def test_viewer_is_self_contained_and_rebuildable(self):
        from norway_agent.cli import main
        out = self.tmp / "out"
        html_text = (out / "index.html").read_text()
        self.assertIn('font-family:"Public Sans";src:url(data:font/woff2;base64,', html_text)
        self.assertNotIn("__F400__", html_text)
        self.assertIn("Source extract", html_text)
        self.assertNotIn("http://fonts", html_text)  # works offline
        rebuilt = out / "rebuilt.html"
        self.assertEqual(main(["viewer", "--profiles", str(out / "profiles.jsonl"), "--report",
                               str(out / "run-report.json"), "--html", str(rebuilt)]), 0)
        self.assertIn("FJORDTECH AS", rebuilt.read_text())

    def test_outputs_written(self):
        out = self.tmp / "out"
        self.assertTrue((out / "index.html").exists())
        self.assertIn("FJORDTECH AS", (out / "index.html").read_text())
        self.assertEqual(len((out / "profiles.jsonl").read_text().splitlines()), 14)
        rep = json.loads((out / "run-report.json").read_text())
        self.assertEqual(rep["third_party_cost_usd"], 0.0)
        self.assertTrue(rep["expected_count_met"])


class RefreshTest(unittest.TestCase):
    def test_rerun_same_snapshot_is_idempotent(self):
        tmp = Path(tempfile.mkdtemp())
        _, first, _ = do_run(tmp, run_id="r1")
        rep, second, _ = do_run(tmp, previous=tmp / "out" / "envelopes.jsonl", run_id="r2")
        self.assertEqual(rep["changes"]["total"], 0)
        a = second[fx.A]
        self.assertEqual(a["refresh"]["previous_run_id"], "r1")
        self.assertIn("No material changes", a["summary"]["what_changed"])
        lead = claim(a, "leadership")
        self.assertLess(lead["first_seen_at"], "9999")  # kept from the first run
        self.assertEqual([c["field"] for c in first[fx.A]["claims"]], [c["field"] for c in a["claims"]])

    def test_change_detection_and_carry_forward(self):
        tmp = Path(tempfile.mkdtemp())
        do_run(tmp, run_id="r1")
        net = fx.FakeInternet()
        r = fx.roles(fx.A)
        r["rollegrupper"][1]["roller"].append({"type": {"kode": "MEDL", "beskrivelse": "Styremedlem"},
                                                "person": {"navn": {"fornavn": "Nina", "etternavn": "Ny"}}})
        net.roles_override[fx.A] = r
        net.fail.add("regnskapsregisteret/regnskap/" + fx.A)
        net.enheter[fx.A]["forretningsadresse"]["adresse"] = ["Strandkaien 9"]
        _, envs, _ = do_run(tmp, net=net, previous=tmp / "out" / "envelopes.jsonl", run_id="r2")
        a = envs[fx.A]
        types = {(c["type"], c["field"]) for c in a["changes"]}
        self.assertIn(("new_role", "leadership"), types)
        self.assertIn(("changed_address", "business_address"), types)
        rev = claim(a, "revenue")
        self.assertTrue(rev.get("stale"))
        self.assertEqual(rev["carried_forward_from"], "r1")
        self.assertEqual(rev["value"]["amount"], 15000000.0)  # failed refresh did not erase good data
        ev_ids = {e["id"] for e in a["evidence"]}
        self.assertTrue(set(rev["evidence_ids"]) <= ev_ids)
        n, problems, _ = validate(tmp / "out" / "envelopes.jsonl")
        self.assertEqual(problems, [])


class EdgeTest(unittest.TestCase):
    def test_unreachable_site_is_failed_not_blocked_and_batch_neighbours_do_not_cause_changes(self):
        tmp = Path(tempfile.mkdtemp())
        do_run(tmp, run_id="r1")
        net = fx.FakeInternet()
        net.fail.add("bergenbygg.example")             # declared site unreachable this time
        net.enheter[fx.B]["navn"] = "NORDLYS INVEST AS"  # a neighbour in the same batch changes
        _, envs, _ = do_run(tmp, net=net, previous=tmp / "out" / "envelopes.jsonl", run_id="r2")
        self.assertEqual(claim(envs[fx.C], "official_website")["availability"], "failed")
        self.assertEqual([c for c in envs[fx.A]["changes"] if c["field"] != "public_activity"], [])
        self.assertFalse([c for c in envs[fx.A]["changes"] if c["field"] == "public_activity"])

    def test_starter_kit_entry_point(self):
        import subprocess, sys
        r = subprocess.run([sys.executable, "scripts/run_competition_batch.py", "--help"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertIn("--organisations", r.stdout)


class BudgetTest(unittest.TestCase):
    def test_tiny_budget_still_one_envelope_each(self):
        tmp = Path(tempfile.mkdtemp())
        cfg = Config(max_requests=3)
        rep, envs, _ = do_run(tmp, cfg=cfg)
        self.assertEqual(len(envs), 14)
        self.assertTrue(rep["one_terminal_envelope_per_input"])
        n, problems, _ = validate(tmp / "out" / "envelopes.jsonl")
        self.assertEqual(problems, [])

    def test_budget_scales_with_batch(self):
        self.assertEqual(Config().resolve(100).max_requests, 2000)
        self.assertEqual(Config().resolve(1000).max_requests, 18200)
        with mock.patch.dict("os.environ", {"SIGNALPOST_MAX_REQUESTS": "777"}):
            self.assertEqual(Config().resolve(1000).max_requests, 777)


class UnitTest(unittest.TestCase):
    def test_inputs(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "a.txt").write_text("orgnr\n923 609 016\nNO 984851006 MVA\n923609016\nabc\n")
        ins, dups = read_batch(str(tmp / "a.txt"))
        self.assertEqual(ins, ["923609016", "984851006", "abc"])
        self.assertEqual(dups, ["923609016"])
        (tmp / "b.jsonl.gz").write_bytes(gzip.compress(b'{"orgnr":"923609016","name":"x"}\n{"organisasjonsnummer":984851006}\n'))
        self.assertEqual(read_batch(str(tmp / "b.jsonl.gz"))[0], ["923609016", "984851006"])

    def test_names(self):
        self.assertEqual(match_key("Fjordtech AS"), match_key("FJORDTECH AS"))
        self.assertEqual(match_key("Møller & Sønn AS"), "MØLLER OG SØNN")
        self.assertTrue(page_names_company("BERGEN BYGG AS", "Velkommen til Bergen Bygg"))
        self.assertFalse(page_names_company("BERGEN BYGG AS", "Bergen kommune"))
        self.assertIn("mollersonn.no", domain_candidates("MØLLER SØNN AS"))
        self.assertEqual(domain_candidates("A AS"), [])

    def test_orgnr_patterns_and_labels(self):
        raw = f"Org.nr: {fx.fmt(fx.A)} · tel 55 12 34 56 · {fx.NAMESAKE}"
        self.assertTrue(web.orgnr_pattern(fx.A).search(raw))
        self.assertEqual(web.labelled_orgnrs(raw), {fx.A})
        self.assertEqual(web.labelled_orgnrs(f"org.nr. {fx.NAMESAKE} MVA"), {fx.NAMESAKE})

    def test_dates(self):
        self.assertEqual(web._date("12. mars 2026"), "2026-03-12")
        self.assertEqual(web._date("Mon, 01 Jan 2018 10:00:00 +0000"), "2018-01-01")
        self.assertEqual(web._date("03.04.2025"), "2025-04-03")
        self.assertEqual(web._date("March 5, 2026"), "2026-03-05")


def _page(url: str, html: str):
    from norway_agent.htmlparse import parse
    from norway_agent.http import Response
    return (Response(url=url, final_url=url, status=200, headers={"content-type": "text/html"},
                     body=html.encode(), retrieved_at="2026-11-06T00:00:00Z", sha256="x"), parse(html))


class DerivedFactsTest(unittest.TestCase):
    """Facts read off a verified website must belong to the company, not to its website builder,
    web agency, careers-page navigation or parent group (cases seen in the 1,000-company run)."""

    def test_social_profiles_must_carry_the_company_name(self):
        home = "https://www.solhjem.example/"
        html = """<html><body>
        <a href="https://www.facebook.com/wix">Wix</a> <a href="https://www.twitter.com/wix">t</a>
        <a href="https://www.linkedin.com/company/wix-com">in</a>
        <a href="https://www.facebook.com/maxroyreklameogmedia">Nettside levert av Maxroy</a>
        <a href="https://www.facebook.com/policy.php">Policy</a> <a href="https://x.com/privacy">Privacy</a>
        <a href="https://www.facebook.com/SolHjemEiendom">Facebook</a>
        <a href="https://instagram.com/sol_hjem">Instagram</a> <a href="https://www.instagram.com/sol_hjem/">dup</a>
        <a href="https://www.linkedin.com/company/12345678/admin">LinkedIn</a>
        </body></html>"""
        out = web._socials([_page(home, html)], home, ["SOL-HJEM AS"], "solhjem")
        urls = sorted(i["url"] for items in out.values() for i in items)
        self.assertEqual(urls, ["https://instagram.com/sol_hjem", "https://www.facebook.com/SolHjemEiendom",
                                "https://www.linkedin.com/company/12345678"])

    def test_numeric_profile_only_from_homepage_and_alone(self):
        home = "https://www.fjordtech.example/"
        deep = "https://www.fjordtech.example/partnere"
        html = '<a href="https://www.facebook.com/profile.php?id=100064255646668">fb</a>'
        self.assertIn("facebook", web._socials([_page(home, html)], home, ["FJORDTECH AS"], "fjordtech"))
        self.assertNotIn("facebook", web._socials([_page(deep, html)], home, ["FJORDTECH AS"], "fjordtech"))
        both = html + '<a href="https://www.facebook.com/fjordtech">fb</a>'
        got = web._socials([_page(home, both)], home, ["FJORDTECH AS"], "fjordtech")["facebook"]
        self.assertEqual([i["url"] for i in got], ["https://www.facebook.com/fjordtech"])

    def test_careers_navigation_is_not_a_job(self):
        url = "https://www.knmaskin.example/karriere"
        html = """<html><body><a href="#main">Hopp til innhold</a>
        <a href="https://www.knmaskin.example/karriere">Karriere</a>
        <a href="/karriere/ledige-stillinger">Se våre ledige stillinger</a>
        <a href="/karriere/slik-er-det-a-jobbe-hos-oss">Slik er det å jobbe hos KN Maskin</a>
        <a href="/karriere/skapte-sin-egen-stilling">Skapte sin egen stilling i kundesenteret</a>
        <a href="/karriere/learning">Learning &amp; Development</a>
        <a href="/stillinger/maskinforer-2026">Er du vår nye maskinfører?</a>
        <a href="/stillinger/lager-123">Lagermedarbeider deltid Se stilling</a>
        <a href="https://knmaskin.teamtailor.com/jobs/4242-anleggsleder">Anleggsleder</a>
        </body></html>"""
        jobs = web._job_links(_page(url, html)[1], _page(url, html)[0], "knmaskin.example")
        self.assertEqual([j["title"] for j in jobs],
                         ["Er du vår nye maskinfører?", "Lagermedarbeider deltid", "Anleggsleder"])

    def test_job_boards_are_never_a_careers_page(self):
        own = "knmaskin.example"
        self.assertFalse(web.careers_link_ok(
            "https://arbeidsplassen.nav.no/stillinger/stilling/acc50110-8b72-4ed5-814b-4c9b6e929338", own))
        self.assertFalse(web.careers_link_ok("https://www.finn.no/job/ad/428301800", own))
        self.assertTrue(web.careers_link_ok("https://knmaskin.teamtailor.com/jobs", own))
        self.assertTrue(web.careers_link_ok("https://www.knmaskin.example/karriere", own))

    def test_external_recruitment_page_counts_only_its_own_postings(self):
        url = "https://knmaskin.teamtailor.com/jobs"
        html = """<html><body>
        <a href="https://knmaskin.teamtailor.com/jobs/1001-maskinforer">Maskinfører</a>
        <a href="https://other.teamtailor.com/jobs/2002-kokk">Kokk</a>
        <a href="https://arbeidsplassen.nav.no/stillinger/stilling/11111111-2222-3333-4444-555555555555">
          Gårdsarbeider og sjåfør – Turistgård – Gimsøysand</a></body></html>"""
        r, d = _page(url, html)
        self.assertEqual([j["title"] for j in web._job_links(d, r, "knmaskin.example")], ["Maskinfører"])

    def test_profile_needs_the_distinctive_name_not_an_industry_word(self):
        home = "https://mucha.example/"
        html = """<a href="https://www.facebook.com/DoctorMucha">fb</a>
        <a href="https://www.facebook.com/dentalsenseoslo">fb2</a>
        <a href="https://www.instagram.com/dentalsenseoslo">ig</a>"""
        out = web._socials([_page(home, html)], home, ["MUCHA DENTAL AS"], "mucha")
        self.assertEqual([i["url"] for items in out.values() for i in items], ["https://www.facebook.com/DoctorMucha"])

    def test_group_and_brand_sites_are_recognised(self):
        def doc(title, site_name=""):
            meta = f'<meta property="og:site_name" content="{site_name}">' if site_name else ""
            return _page("https://x.example/", f"<html><head><title>{title}</title>{meta}</head></html>")[1]
        r = web.shared_site_reason
        thon = doc("Thon Gruppen – Bolig, eiendom, hotell og kjøpesentre | Thon")
        self.assertIn("only part of the legal name", r("THON STORO AS", "www.thon.no", "https://thon.no/", thon,
                                                       "declared_by_registry+name_on_site", True))
        self.assertIn("country section", r("XEROX AS", "", "https://www.xerox.com/no-no", doc("Xerox"),
                                           "name_guess+name_and_registered_contact_on_site", True))
        self.assertIn("section", r("AXFLOW SANDNES AS", "www.axflow.com/nb-no", "https://www.axflow.com/nb-no/",
                                   doc("AxFlow Sandnes"), "declared_by_registry+name_on_site", True))
        self.assertIsNone(r("MYHRE ELEKTRO AS", "www.myhre-elektro.no", "https://www.myhre-elektro.no/",
                            doc("Hjem - Myhre Elektro - din elektriker i Skarnes", "Myhre Elektro"),
                            "declared_by_registry+name_on_site", True))
        self.assertIsNone(r("BRANDÅS TRANSPORT AS", "", "https://brandastransport.no/", doc("Brandås"),
                            "name_guess+name_and_registered_contact_on_site", True))  # domain = full name
        self.assertIsNone(r("THON STORO AS", "www.thon.no", "https://thon.no/", thon, "org_number_on_site", True))

    def test_group_site_content_is_not_attributed(self):
        group_home = """<html><head><title>Skogly – IT for bedrifter | Skogly</title>
        <meta property="og:site_name" content="Skogly"><meta name="description" content="Skogly-konsernet leverer IT.">
        <script type="application/ld+json">{"@type":"JobPosting","title":"Konsernsjef",
        "hiringOrganization":{"@type":"Organization","name":"Skogly"}}</script></head>
        <body><h1>Skogly</h1><a href="https://www.facebook.com/skogly">Facebook</a>
        <a href="mailto:post@skogly.example">post@skogly.example</a></body></html>"""

        class GroupNet(fx.FakeInternet):
            def web(self, url, host, path):
                if host == "skogly.example":
                    return self.h("", 404) if path == "/robots.txt" else self.h(group_home)
                return super().web(url, host, path)
        net = GroupNet()
        net.enheter[fx.K]["hjemmeside"] = "skogly.example"
        tmp = Path(tempfile.mkdtemp())
        _, envs, _ = do_run(tmp, net=net)
        k = envs[fx.K]
        site = claim(k, "official_website")
        self.assertEqual(site["availability"], "available")
        self.assertTrue(site["value"]["shared_with_group"])
        for fld in ("social_profiles", "website_description", "website_contact", "public_brand_name"):
            self.assertEqual(claim(k, fld)["availability"], "ambiguous", fld)
        titles = [j["title"] for j in claim(k, "job_postings")["value"]]
        self.assertNotIn("Konsernsjef", titles)  # the group's posting
        self.assertIn("Systemutvikler", titles)  # NAV ad tied to K's own org number still counts
        n, problems, _ = validate(tmp / "out" / "envelopes.jsonl")
        self.assertEqual(problems, [])


class AddressProofTest(unittest.TestCase):
    """A site may be tied to the company by its registered street address (with house number) and postcode."""

    def test_patterns(self):
        from norway_agent.web import _address_on, _address_patterns
        pats = _address_patterns({"forretningsadresse": {"adresse": ["Stabells gate 13"], "postnummer": "3510"}})
        for ok in ("Stabells gate 13, 3510 Hønefoss", "Stabellsgate 13<br>3510 HØNEFOSS", "stabells gt. 13 - 3510"):
            self.assertTrue(_address_on(ok, pats), ok)
        for bad in ("Stabells gate 131, 3510 Hønefoss", "Stabells gate 13, 3511 Hønefoss", "Tinley Park, IL 60477"):
            self.assertFalse(_address_on(bad, pats), bad)
        self.assertEqual(_address_patterns({"forretningsadresse": {"adresse": ["Postboks"], "postnummer": "0101"}}), [])

    def _run_with_guess_page(self, html):
        class GuessNet(fx.FakeInternet):
            def web(self, url, host, path):
                if host in ("nordlysholding.no", "www.nordlysholding.no"):
                    return self.h("", 404) if path == "/robots.txt" else self.h(html)
                return super().web(url, host, path)
        tmp = Path(tempfile.mkdtemp())
        _, envs, _ = do_run(tmp, net=GuessNet())
        n, problems, _ = validate(tmp / "out" / "envelopes.jsonl")
        self.assertEqual(problems, [])
        return envs[fx.B]

    def test_guessed_site_with_name_and_registered_address_is_verified(self):
        b = self._run_with_guess_page("""<html><head><title>Nordlys Holding</title></head><body><h1>Nordlys Holding</h1>
        <footer>Nordlys Holding AS, Bryggen 1, 5003 Bergen</footer></body></html>""")
        site = claim(b, "official_website")
        self.assertEqual(site["availability"], "available")
        self.assertEqual(site["value"]["verification"], "name_guess+name_and_registered_contact_on_site")
        ev = {e["id"]: e for e in b["evidence"]}
        self.assertTrue(any("Bryggen 1" in ev[i]["claim_span"] for i in site["evidence_ids"]))

    def test_distinctive_word_domain_needs_the_org_number(self):
        # nordlys.no for NORDLYS HOLDING AS: often the operating or sister company at the same address.
        from unittest import mock as _m
        page = """<html><head><title>Nordlys</title></head><body><h1>Nordlys Holding</h1>
        <footer>Nordlys AS, Bryggen 1, 5003 Bergen</footer></body></html>"""

        class CoreNet(fx.FakeInternet):
            def web(self, url, host, path):
                if host in ("nordlys.no", "www.nordlys.no"):
                    return self.h("", 404) if path == "/robots.txt" else self.h(page)
                if host in ("nordlysholding.no", "www.nordlysholding.no"):
                    raise OSError("Name or service not known")
                return super().web(url, host, path)
        def run_with(html):
            nonlocal page
            page = html
            net = CoreNet()
            tmp = Path(tempfile.mkdtemp())
            with _m.patch("tests.test_agent.fake_resolves", lambda h, timeout=3.0: h in ("nordlys.no", "www.nordlys.no")):
                _, envs, _ = do_run(tmp, net=net)
            self.assertTrue(any("nordlys.no" in c for c in net.calls))  # the guess was really tried
            return envs[fx.B]
        b = run_with(page)
        self.assertNotEqual(claim(b, "official_website")["availability"], "available")
        self.assertNotIn("nordlys.no", json.dumps(b).lower())
        b = run_with(page.replace("</footer>", f" · Nordlys Holding AS org.nr. {fx.fmt(fx.B)}</footer>"))
        self.assertEqual(claim(b, "official_website")["availability"], "available")  # the org number ties it

    def test_namesake_at_another_address_is_not_verified(self):
        b = self._run_with_guess_page("""<html><head><title>Nordlys Holding</title></head><body><h1>Nordlys Holding</h1>
        <footer>Nordlys Holding AS, Bryggen 12, 5003 Bergen</footer></body></html>""")
        self.assertEqual(claim(b, "official_website")["availability"], "not_available")
        self.assertNotIn("nordlysholding", json.dumps(b).lower())


class SectionProbeTest(unittest.TestCase):
    """Careers and news pages found at common paths when the homepage menu does not link them."""

    @staticmethod
    def _net(pages, soft404=False):
        home = "<html><head><title>Glimt</title></head><body><h1>Data og analyse</h1></body></html>"

        class SectionNet(fx.FakeInternet):
            def web(self, url, host, path):
                if host in ("glimtanalyse.com", "www.glimtanalyse.com"):
                    if path == "/kontakt":
                        return self.h(f"<html><body>Glimt Analyse AS · Org.nr. {fx.fmt(fx.M)} · Oslo</body></html>")
                    if path in pages:
                        return self.h(pages[path])
                    if path == "/" or (soft404 and path != "/robots.txt"):
                        return self.h(home)
                    return self.h("not found", 404)
                return super().web(url, host, path)
        return SectionNet()

    def test_probed_careers_and_news_with_article_dates(self):
        d1, d2 = fx.DAYS(20), fx.DAYS(90)
        art = lambda title, d: (f'<html><head><meta property="og:title" content="{title}">'  # noqa: E731
                                f'<meta property="article:published_time" content="{d}T08:00:00+00:00">'
                                f'<meta property="article:modified_time" content="{fx.DAYS(1)}T08:00:00+00:00">'
                                f'</head><body><article><h1>{title}</h1><p>Tekst.</p></article></body></html>')
        pages = {
            "/karriere": """<html><head><title>Karriere | Glimt</title></head><body><h1>Jobb hos oss</h1>
                <a href="/karriere/dataanalytiker-heltid">Dataanalytiker (heltid)</a>
                <a href="/karriere/mot-kari-prosjektleder">Møt Kari, prosjektleder</a>
                <a href="/karriere/radgiver-heltid-2024">Rådgiver (heltid)</a></body></html>""",
            "/karriere/dataanalytiker-heltid": """<html><body><h1>Dataanalytiker (heltid)</h1>
                <p>Arbeidssted: Oslo. Søknadsfrist: snarest.</p></body></html>""",
            "/karriere/mot-kari-prosjektleder": """<html><body><h1>Møt Kari</h1><p>Kari har jobbet her i fem år.</p>
                </body></html>""",
            "/karriere/radgiver-heltid-2024": """<html><head><script type="application/ld+json">{"@type":"JobPosting",
                "title":"Rådgiver","datePosted":"2024-01-02","validThrough":"2024-02-01",
                "hiringOrganization":{"@type":"Organization","name":"Glimt Analyse AS"}}</script></head>
                <body><h1>Rådgiver (heltid)</h1></body></html>""",
            "/nyheter": """<html><head><title>Nyheter | Glimt</title></head><body><h1>Nyheter</h1>
                <a href="/nyheter/ny-avtale-med-fylket">Ny avtale med fylket</a>
                <a href="/nyheter/vi-flytter-til-bjorvika">Vi flytter til Bjørvika</a>
                <a href="/nyheter/page/2">Eldre</a></body></html>""",
            "/nyheter/ny-avtale-med-fylket": art("Ny avtale med fylket", d1),
            "/nyheter/vi-flytter-til-bjorvika": art("Vi flytter til Bjørvika", d2),
        }
        tmp = Path(tempfile.mkdtemp())
        _, envs, _ = do_run(tmp, net=self._net(pages))
        m = envs[fx.M]
        self.assertEqual(claim(m, "official_website")["availability"], "available")
        news = claim(m, "website_news")
        self.assertEqual(news["availability"], "available")
        self.assertEqual({(n["date"], n["title"]) for n in news["value"]},
                         {(d1, "Ny avtale med fylket"), (d2, "Vi flytter til Bjørvika")})  # published, not modified
        self.assertEqual(claim(m, "careers_page")["availability"], "available")
        titles = [j["title"] for j in claim(m, "job_postings")["value"]]
        self.assertIn("Dataanalytiker (heltid)", titles)
        self.assertNotIn("Møt Kari, prosjektleder", titles)  # a staff story, not a posting
        self.assertNotIn("Rådgiver (heltid)", titles)  # its JobPosting data says it closed in 2024
        n, problems, _ = validate(tmp / "out" / "envelopes.jsonl")
        self.assertEqual(problems, [])

    def test_soft_404_is_not_a_section(self):
        tmp = Path(tempfile.mkdtemp())
        _, envs, _ = do_run(tmp, net=self._net({}, soft404=True))
        m = envs[fx.M]
        self.assertEqual(claim(m, "official_website")["availability"], "available")
        self.assertNotEqual(claim(m, "careers_page")["availability"], "available")
        self.assertNotEqual(claim(m, "website_news")["availability"], "available")


class AuditFindingsTest(unittest.TestCase):
    """Cases found in the hand audit of the 1,000-company v0.2.8 run."""

    def test_news_items_are_articles(self):
        from norway_agent.web import _news_item_ok

        class Home:
            final_url = "https://www.example.no/"
        ok = lambda u, t="Vi åpner ny butikk i Bergen": _news_item_ok({"url": u, "title": t}, Home)  # noqa: E731
        self.assertTrue(ok("https://www.example.no/blog/2026/4/27/ny-butikk"))
        self.assertFalse(ok("https://www.example.no/blog?author=5a788cef"))  # an author listing
        self.assertFalse(ok("https://www.example.no/nyheter/tag/bakst"))
        self.assertFalse(ok("https://www.example.no/"))  # the homepage itself
        self.assertFalse(ok("https://www.example.no/2026/06/10/x", "The Rise of Online Gaming Sites"))  # spam post

    def test_careers_wording(self):
        from norway_agent.web import CAREER_SLUG, CAREER_WORDS
        self.assertFalse(CAREER_WORDS.search("Bli medlem"))  # membership, not a job
        self.assertFalse(CAREER_WORDS.search("Rekruttering"))  # a recruitment service the company sells
        self.assertTrue(CAREER_WORDS.search("Bli med oss"))
        self.assertTrue(CAREER_SLUG.fullmatch("jobbe-i-spera"))
        self.assertFalse(CAREER_SLUG.fullmatch("store-tilbake-pa-jobb-etter-omgangssyken"))

    def test_holding_company_does_not_take_its_subsidiarys_site(self):
        from norway_agent.web import HOLDING_WORDS
        from norway_agent.names import tokens
        self.assertTrue(set(tokens("KRONBERG HOLDING AS")) & HOLDING_WORDS)
        self.assertFalse(set(tokens("BOLIGLEVERANDØREN KARMØY AS")) & HOLDING_WORDS)


class SiteDataTest(unittest.TestCase):
    """profiles.jsonl (read by the JBOX site) gets plain-English display text, with every key, list
    length and number unchanged, so a site reading the old shape keeps working."""

    def test_site_card_changes_display_text_only(self):
        from norway_agent.viewer import _nice_dates, card, site_card
        tmp = Path(tempfile.mkdtemp())
        _, envs, _ = do_run(tmp)

        def nums(x, acc):
            if isinstance(x, bool):
                return acc
            if isinstance(x, (int, float)):
                acc.append(x)
            elif isinstance(x, dict):
                [nums(v, acc) for v in x.values()]
            elif isinstance(x, list):
                [nums(v, acc) for v in x]
            return acc
        for env in envs.values():
            raw, pub = card(env), site_card(card(env))
            self.assertEqual(set(raw), set(pub))
            self.assertEqual(set(raw["facts"]), set(pub["facts"]))
            for k in raw:
                if k not in ("facts", "summary"):
                    self.assertEqual(raw[k], pub[k], k)
            if raw.get("summary_plain"):
                self.assertEqual(pub["summary"], _nice_dates(raw["summary_plain"]))
            for f, x in raw["facts"].items():
                y = pub["facts"][f]
                for k in ("label", "state", "ev", "note", "stale", "reason"):
                    self.assertEqual(x.get(k), y.get(k), (f, k))
                self.assertEqual(nums(x.get("value"), []), nums(y.get("value"), []), f)
                if isinstance(x.get("value"), list):
                    self.assertEqual(len(x["value"]), len(y["value"]), f)
        a = site_card(card(envs[fx.A]))["facts"]
        roles = [i["role"] for i in a["leadership"]["value"]]
        self.assertIn("Chief executive", roles)
        self.assertTrue(all("group" not in i for i in a["leadership"]["value"]))
        self.assertNotRegex(a["founding_date"]["text"], r"\d{4}-\d{2}-\d{2}")
        self.assertEqual(a["registry_status"]["text"], "Active")

    def test_published_file_is_the_display_version(self):
        tmp = Path(tempfile.mkdtemp())
        do_run(tmp)
        rows = [json.loads(l) for l in (tmp / "out" / "profiles.jsonl").read_text(encoding="utf-8").splitlines()]
        a = next(r for r in rows if r["org"] == fx.A)
        self.assertEqual(a["facts"]["registry_status"]["text"], "Active")


class RetryTest(unittest.TestCase):
    """A guessed domain that does not exist must fail at once on the web lane (no wasted retry),
    while register/NAV calls keep retrying transient network errors."""

    def _http(self, exc):
        import urllib.error
        from norway_agent.http import Http
        calls = []

        def opener(url, headers, timeout, max_bytes):
            calls.append(url)
            raise urllib.error.URLError(exc)
        cfg = Config(max_requests=100)
        return Http(cfg, opener=opener, sleep=lambda s: None), calls

    def test_dns_failure_not_retried_on_web(self):
        import socket
        http, calls = self._http(socket.gaierror(socket.EAI_NONAME, "Name or service not known"))
        r = http.get("https://does-not-exist-example.no/", lane="web")
        self.assertEqual(len(calls), 1)
        self.assertEqual(r.status, 0)
        self.assertIn("network error", r.error)

    def test_timeout_still_retried_on_web(self):
        http, calls = self._http(TimeoutError("timed out"))
        http.get("https://slow.example.no/", lane="web")
        self.assertEqual(len(calls), Config().web_attempts)

    def test_api_lane_keeps_retrying(self):
        import socket
        http, calls = self._http(socket.gaierror(socket.EAI_NONAME, "Name or service not known"))
        http.get("https://data.brreg.no/enhetsregisteret/api/enheter/123456785", lane="brreg")
        self.assertEqual(len(calls), Config().api_attempts)


if __name__ == "__main__":
    unittest.main()
