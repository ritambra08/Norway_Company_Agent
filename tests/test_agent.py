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

    def test_parent_site_is_ambiguous_not_merged(self):
        c = self.envs[fx.C]
        w = claim(c, "official_website")
        self.assertEqual(w["availability"], "ambiguous")
        self.assertIn(fx.PARENT_C, w["reason"])
        self.assertTrue(c["abstentions"])

    def test_guessed_namesake_site_is_discarded(self):
        b = self.envs[fx.B]
        self.assertEqual(claim(b, "official_website")["availability"], "not_available")
        self.assertNotIn("nordlysholding", json.dumps(claim(b, "social_profiles")))

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


if __name__ == "__main__":
    unittest.main()
