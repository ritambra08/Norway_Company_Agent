"""Synthetic API responses shaped like the Brønnøysund APIs. Not real companies."""

from __future__ import annotations

import json
from urllib.parse import urlencode

from norway_agent.config import Config
from norway_agent.orgnr import check_digit

BASE = Config().base_url


def make_orgnr(prefix8: int) -> str:
    """Smallest valid org number whose first 8 digits are >= prefix8."""
    while True:
        s = f"{prefix8:08d}"
        d = check_digit(s)
        if d is not None:
            return s + str(d)
        prefix8 += 1


ORG = make_orgnr(91234567)
OTHER = make_orgnr(98765432)
SUB = make_orgnr(81234567)
PARENT = make_orgnr(91111111)


def enhet(orgnr=ORG, **over):
    d = {
        "organisasjonsnummer": orgnr,
        "navn": "TESTSELSKAP AS",
        "organisasjonsform": {"kode": "AS", "beskrivelse": "Aksjeselskap"},
        "registreringsdatoEnhetsregisteret": "2015-03-02",
        "registrertIMvaregisteret": True,
        "registrertIForetaksregisteret": True,
        "naeringskode1": {"beskrivelse": "Utvikling av programvare", "kode": "62.010"},
        "antallAnsatte": 12,
        "harRegistrertAntallAnsatte": True,
        "hjemmeside": "www.testselskap.example",
        "forretningsadresse": {"land": "Norge", "landkode": "NO", "postnummer": "0150", "poststed": "OSLO",
                               "adresse": ["Testgata 1"], "kommune": "OSLO", "kommunenummer": "0301"},
        "sisteInnsendteAarsregnskap": "2024",
        "konkurs": False,
        "underAvvikling": False,
        "underTvangsavviklingEllerTvangsopplosning": False,
    }
    d.update(over)
    return d


def underenhet(orgnr=SUB, parent=PARENT, **over):
    d = {
        "organisasjonsnummer": orgnr,
        "navn": "TESTSELSKAP AVD BERGEN",
        "organisasjonsform": {"kode": "BEDR", "beskrivelse": "Underenhet til næringsdrivende og offentlig forvaltning"},
        "registreringsdatoEnhetsregisteret": "2018-05-01",
        "overordnetEnhet": parent,
        "antallAnsatte": 3,
        "harRegistrertAntallAnsatte": True,
        "beliggenhetsadresse": {"land": "Norge", "landkode": "NO", "postnummer": "5003", "poststed": "BERGEN",
                                "adresse": ["Bryggen 2"], "kommune": "BERGEN", "kommunenummer": "4601"},
    }
    d.update(over)
    return d


def roller():
    return {"rollegrupper": [
        {"type": {"kode": "STYR", "beskrivelse": "Styre"}, "roller": [
            {"type": {"kode": "LEDE", "beskrivelse": "Styrets leder"}, "person": {
                "fodselsdato": "1970-01-01", "navn": {"fornavn": "Kari", "etternavn": "Nordmann"}},
             "fratraadt": False},
            {"type": {"kode": "MEDL", "beskrivelse": "Styremedlem"}, "person": {
                "fodselsdato": "1980-02-02", "navn": {"fornavn": "Ola", "etternavn": "Gammel"}},
             "fratraadt": True},
        ]},
        {"type": {"kode": "REVI", "beskrivelse": "Revisor"}, "roller": [
            {"type": {"kode": "REVI", "beskrivelse": "Revisor"},
             "enhet": {"organisasjonsnummer": OTHER, "navn": ["REVISJON AS"]}, "fratraadt": False},
        ]},
    ]}


def sub_list(parent=ORG, extra_foreign=False):
    items = [{"organisasjonsnummer": SUB, "navn": "TESTSELSKAP AVD BERGEN", "overordnetEnhet": parent,
              "antallAnsatte": 3, "harRegistrertAntallAnsatte": True,
              "beliggenhetsadresse": {"kommune": "BERGEN"}}]
    if extra_foreign:
        items.append({"organisasjonsnummer": OTHER, "navn": "FOREIGN", "overordnetEnhet": PARENT})
    return {"_embedded": {"underenheter": items}, "page": {"totalElements": len(items)}}


def regnskap_entry(orgnr=ORG, til="2024-12-31", revenue=1_500_000, net=120_000, currency="NOK", typ="SELSKAP"):
    return {
        "id": "x", "regnskapstype": typ, "virksomhet": {"organisasjonsnummer": orgnr, "organisasjonsform": "AS", "morselskap": False},
        "regnskapsperiode": {"fraDato": til[:4] + "-01-01", "tilDato": til},
        "valuta": currency, "avviklingsregnskap": False, "oppstillingsplan": "store",
        "regnkapsprinsipper": {"smaaForetak": True, "regnskapsregler": "regnskapslovenAlminneligRegler"},
        "egenkapitalGjeld": {"sumEgenkapitalGjeld": 900_000, "egenkapital": {"sumEgenkapital": 400_000},
                             "gjeldOversikt": {"sumGjeld": 500_000}},
        "eiendeler": {"sumEiendeler": 900_000},
        "resultatregnskapResultat": {"aarsresultat": net,
                                     "driftsresultat": {"driftsresultat": 150_000,
                                                        "driftsinntekter": {"sumDriftsinntekter": revenue}}},
    }


class FakeServer:
    """Maps exact URLs to (status, body). Unknown URLs are 404."""

    def __init__(self):
        self.routes: dict[str, tuple[int, object]] = {}
        self.calls: list[str] = []

    def add(self, url, body, status=200):
        self.routes[url] = (status, body)

    def standard(self, orgnr=ORG, enh=None, with_accounts=True):
        self.add(f"{BASE}/enhetsregisteret/api/enheter/{orgnr}", enh or enhet(orgnr))
        self.add(f"{BASE}/enhetsregisteret/api/enheter/{orgnr}/roller", roller())
        q = urlencode({"overordnetEnhet": orgnr, "size": 100})
        self.add(f"{BASE}/enhetsregisteret/api/underenheter?{q}", sub_list(orgnr))
        if with_accounts:
            self.add(f"{BASE}/regnskapsregisteret/regnskap/{orgnr}",
                     [regnskap_entry(orgnr)])

    def opener(self, url, headers, timeout):
        self.calls.append(url)
        status, body = self.routes.get(url, (404, {}))
        return status, {}, json.dumps(body).encode()
