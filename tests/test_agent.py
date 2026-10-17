import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlencode

from norway_agent import orgnr as om
from norway_agent.build import build_profile
from norway_agent.config import Config
from norway_agent.http import Http
from norway_agent.inputs import read_orgnrs
from norway_agent.store import Store
from norway_agent.validate import validate_profile

from .fixtures import (BASE, ORG, OTHER, PARENT, SUB, FakeServer, enhet, regnskap_entry,
                       sub_list, underenhet)


def make(cfg=None, server=None):
    server = server or FakeServer()
    cfg = cfg or Config()
    http = Http(cfg, opener=server.opener, sleep=lambda s: None)
    tmp = tempfile.TemporaryDirectory()
    return server, cfg, http, Store(Path(tmp.name)), tmp


def facts(p):
    return {f["key"]: f["value"] for f in p["facts"]}


class OrgNumber(unittest.TestCase):
    def test_known_valid_and_invalid(self):
        self.assertTrue(om.is_valid("923609016"))
        self.assertFalse(om.is_valid("923609017"))
        self.assertFalse(om.is_valid("12345"))

    def test_normalize(self):
        self.assertEqual(om.normalize("NO 923 609 016 MVA"), "923609016")
        self.assertIsNone(om.normalize("12-34"))

    def test_fixture_numbers_valid(self):
        for o in (ORG, OTHER, SUB, PARENT):
            self.assertTrue(om.is_valid(o), o)


class HappyPath(unittest.TestCase):
    def test_full_profile_with_evidence_and_replay(self):
        s, cfg, http, store, tmp = make()
        s.standard()
        p = build_profile(http, cfg, store, ORG)
        self.assertEqual(p["status"], "ok")
        f = facts(p)
        self.assertEqual(f["name"], "TESTSELSKAP AS")
        self.assertEqual(f["employees"], 12)
        self.assertEqual(f["revenue"], {"amount": 1_500_000, "currency": "NOK",
                                        "period_start": "2024-01-01", "period_end": "2024-12-31"})
        self.assertEqual(f["establishments"]["total"], 1)
        self.assertEqual([r["role_code"] for r in f["roles"]], ["LEDE", "REVI"])  # resigned member excluded
        self.assertNotIn("1970", json.dumps(p))  # birth dates never copied
        for fact in p["facts"]:
            self.assertEqual(fact["subject_orgnr"], ORG)
            self.assertTrue(fact["source"]["url"].startswith("https://data.brreg.no/"))
            self.assertTrue(fact["source"]["retrieved_at"])
        self.assertEqual(validate_profile(p, store), [])  # replay from raw matches
        self.assertEqual(len(s.calls), 4)
        tmp.cleanup()

    def test_tampered_value_is_caught_by_replay(self):
        s, cfg, http, store, tmp = make()
        s.standard()
        p = build_profile(http, cfg, store, ORG)
        for fact in p["facts"]:
            if fact["key"] == "revenue":
                fact["value"]["amount"] = 9_999_999
        problems = validate_profile(p, store)
        self.assertTrue(any("revenue" in x for x in problems), problems)
        tmp.cleanup()


class WrongCompanyGuards(unittest.TestCase):
    def test_enhet_response_for_other_company_publishes_nothing(self):
        s, cfg, http, store, tmp = make()
        s.add(f"{BASE}/enhetsregisteret/api/enheter/{ORG}", enhet(OTHER))
        p = build_profile(http, cfg, store, ORG)
        self.assertEqual(p["status"], "error")
        self.assertEqual(p["facts"], [])
        self.assertEqual(len(s.calls), 1)  # nothing further fetched on a mismatch
        tmp.cleanup()

    def test_foreign_accounts_and_establishments_are_discarded(self):
        s, cfg, http, store, tmp = make()
        s.standard()
        s.add(f"{BASE}/regnskapsregisteret/regnskap?{urlencode({'orgNummer': ORG})}",
              [regnskap_entry(OTHER, revenue=777), regnskap_entry(ORG)])
        q = urlencode({"overordnetEnhet": ORG, "size": 100})
        s.add(f"{BASE}/enhetsregisteret/api/underenheter?{q}", sub_list(ORG, extra_foreign=True))
        p = build_profile(http, cfg, store, ORG)
        f = facts(p)
        self.assertEqual(f["revenue"]["amount"], 1_500_000)
        self.assertEqual([i["orgnr"] for i in f["establishments"]["items"]], [SUB])
        self.assertEqual(len([n for n in p["notes"] if "Discarded" in n]), 2)
        tmp.cleanup()

    def test_conflicting_accounts_for_same_period_publish_no_financials(self):
        s, cfg, http, store, tmp = make()
        s.standard()
        s.add(f"{BASE}/regnskapsregisteret/regnskap?{urlencode({'orgNummer': ORG})}",
              [regnskap_entry(ORG, revenue=1), regnskap_entry(ORG, revenue=2)])
        p = build_profile(http, cfg, store, ORG)
        self.assertNotIn("revenue", facts(p))
        self.assertTrue(any(m["key"] == "financials" and "conflicting" in m["reason"].lower()
                            for m in p["not_found"]))
        tmp.cleanup()

    def test_latest_period_is_chosen_and_missing_currency_blocks_figures(self):
        s, cfg, http, store, tmp = make()
        s.standard()
        url = f"{BASE}/regnskapsregisteret/regnskap?{urlencode({'orgNummer': ORG})}"
        s.add(url, [regnskap_entry(ORG, til="2023-12-31", revenue=5), regnskap_entry(ORG, til="2024-12-31", revenue=6)])
        self.assertEqual(facts(build_profile(http, cfg, store, ORG))["revenue"]["amount"], 6)
        nocur = regnskap_entry(ORG)
        nocur["valuta"] = None
        s.add(url, [nocur])
        self.assertNotIn("revenue", facts(build_profile(http, cfg, store, ORG)))
        tmp.cleanup()


class EntityKinds(unittest.TestCase):
    def test_underenhet_never_requests_accounts_and_links_parent(self):
        s, cfg, http, store, tmp = make()
        s.add(f"{BASE}/enhetsregisteret/api/underenheter/{SUB}", underenhet())
        p = build_profile(http, cfg, store, SUB)
        self.assertEqual(p["entity_type"], "underenhet")
        self.assertEqual(facts(p)["parent_unit_orgnr"], PARENT)
        self.assertFalse(any("regnskap" in c for c in s.calls))
        tmp.cleanup()

    def test_deleted_entity(self):
        s, cfg, http, store, tmp = make()
        s.add(f"{BASE}/enhetsregisteret/api/enheter/{ORG}",
              {"organisasjonsnummer": ORG, "navn": "GAMMEL AS", "slettedato": "2022-01-01"}, status=410)
        p = build_profile(http, cfg, store, ORG)
        self.assertEqual(p["status"], "deleted")
        self.assertEqual(facts(p)["deletion_date"], "2022-01-01")
        tmp.cleanup()

    def test_not_found_and_invalid(self):
        s, cfg, http, store, tmp = make()
        self.assertEqual(build_profile(http, cfg, store, ORG)["status"], "not_found")
        before = len(s.calls)
        self.assertEqual(build_profile(http, cfg, store, "923609017")["status"], "invalid_orgnr")
        self.assertEqual(len(s.calls), before)  # invalid numbers cost zero requests
        tmp.cleanup()

    def test_unregistered_employees_is_missing_not_zero(self):
        s, cfg, http, store, tmp = make()
        s.standard(enh=enhet(antallAnsatte=0, harRegistrertAntallAnsatte=False))
        p = build_profile(http, cfg, store, ORG)
        self.assertNotIn("employees", facts(p))
        self.assertTrue(any(m["key"] == "employees" for m in p["not_found"]))
        tmp.cleanup()

    def test_sole_proprietorship_skips_accounts_request(self):
        s, cfg, http, store, tmp = make()
        s.standard(enh=enhet(organisasjonsform={"kode": "ENK", "beskrivelse": "Enkeltpersonforetak"}))
        build_profile(http, cfg, store, ORG)
        self.assertFalse(any("regnskap" in c for c in s.calls))
        tmp.cleanup()


class Budgets(unittest.TestCase):
    def test_request_budget_gives_partial_not_crash(self):
        s, cfg, http, store, tmp = make(Config(max_requests=2))
        s.standard()
        p = build_profile(http, cfg, store, ORG)
        self.assertEqual(p["status"], "partial")
        self.assertIn("name", facts(p))
        self.assertLessEqual(http.requests, 2)
        tmp.cleanup()

    def test_per_company_cap(self):
        s, cfg, http, store, tmp = make(Config(max_requests_per_company=1))
        s.standard()
        self.assertEqual(build_profile(http, cfg, store, ORG)["status"], "partial")
        self.assertEqual(len(s.calls), 1)
        tmp.cleanup()

    def test_retries_count_against_budget(self):
        s, cfg, http, store, tmp = make(Config(max_requests=3))
        calls = {"n": 0}

        def flaky(url, headers, timeout):
            calls["n"] += 1
            return 503, {}, b""

        http._open = flaky
        p = build_profile(http, cfg, store, ORG)
        self.assertEqual(calls["n"], 3)
        self.assertIn(p["status"], ("error", "skipped_budget"))
        tmp.cleanup()


class Updates(unittest.TestCase):
    def test_change_is_logged_and_unchanged_facts_keep_first_seen(self):
        s, cfg, http, store, tmp = make()
        s.standard()
        first = build_profile(http, cfg, store, ORG)
        s.standard(enh=enhet(antallAnsatte=20))
        second = build_profile(http, cfg, store, ORG)
        self.assertEqual(facts(second)["employees"], 20)
        ch = [c for c in second["change_log"] if c["key"] == "employees"]
        self.assertEqual((ch[0]["old"], ch[0]["new"]), (12, 20))
        name1 = next(f for f in first["facts"] if f["key"] == "name")
        name2 = next(f for f in second["facts"] if f["key"] == "name")
        self.assertEqual(name1["first_seen_at"], name2["first_seen_at"])
        emp2 = next(f for f in second["facts"] if f["key"] == "employees")
        self.assertIsNotNone(emp2["changed_at"])
        tmp.cleanup()

    def test_failed_refresh_keeps_good_data_marked_stale(self):
        s, cfg, http, store, tmp = make()
        s.standard()
        build_profile(http, cfg, store, ORG)
        s.add(f"{BASE}/enhetsregisteret/api/enheter/{ORG}", {}, status=500)
        p = build_profile(http, cfg, store, ORG)
        self.assertTrue(p["stale"])
        self.assertEqual(facts(p)["name"], "TESTSELSKAP AS")
        tmp.cleanup()

    def test_failed_refresh_does_not_overwrite_saved_evidence(self):
        s, cfg, http, store, tmp = make()
        s.standard()
        p = build_profile(http, cfg, store, ORG)
        s.add(f"{BASE}/enhetsregisteret/api/enheter/{ORG}", {}, status=500)
        stale = build_profile(http, cfg, store, ORG)
        self.assertTrue(stale["stale"])
        self.assertEqual(store.load_raw(ORG)["enhet"].status, 200)
        stale["stale"] = False  # replay check applies to fresh data only
        self.assertEqual(validate_profile(p, store), [])
        tmp.cleanup()

    def test_more_forms_skip_accounts_and_extra_financials_are_copied(self):
        s, cfg, http, store, tmp = make()
        s.standard(enh=enhet(organisasjonsform={"kode": "KOMM", "beskrivelse": "Kommune"}))
        build_profile(http, cfg, store, ORG)
        self.assertFalse(any("regnskap" in c for c in s.calls))
        s2, cfg2, http2, store2, tmp2 = make()
        s2.standard()
        e = regnskap_entry(ORG)
        e["eiendeler"]["omloepsmidler"] = {"sumOmloepsmidler": 300_000}
        s2.add(f"{BASE}/regnskapsregisteret/regnskap?{urlencode({'orgNummer': ORG})}", [e])
        self.assertEqual(facts(build_profile(http2, cfg2, store2, ORG))["current_assets"]["amount"], 300_000)
        tmp.cleanup()
        tmp2.cleanup()

    def test_bankruptcy_status_change_is_recorded(self):
        s, cfg, http, store, tmp = make()
        s.standard()
        build_profile(http, cfg, store, ORG)
        s.standard(enh=enhet(konkurs=True))
        p = build_profile(http, cfg, store, ORG)
        self.assertEqual(facts(p)["operating_status"], "bankrupt")
        self.assertTrue(any(c["type"] == "changed" and c["key"] == "operating_status" for c in p["change_log"]))
        tmp.cleanup()


class InputParsing(unittest.TestCase):
    def test_text_csv_json(self):
        with tempfile.TemporaryDirectory() as d:
            t = Path(d, "a.txt")
            t.write_text("orgnr\n923 609 016\nNO 984851006 MVA\n923609016\n12345\n")
            self.assertEqual(read_orgnrs(str(t)), ["923609016", "984851006", "12345"])
            j = Path(d, "a.json")
            j.write_text(json.dumps([{"organisasjonsnummer": "923609016"}, "984851006"]))
            self.assertEqual(read_orgnrs(str(j)), ["923609016", "984851006"])


if __name__ == "__main__":
    unittest.main()
