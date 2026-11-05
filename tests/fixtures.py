"""A fake internet shaped like the real sources (Brønnøysund, NAV feed, company websites).

Response shapes follow live responses captured in October 2026; the companies are synthetic.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

from norway_agent.orgnr import check_digit


def make_orgnr(prefix8: int) -> str:
    while True:
        s = f"{prefix8:08d}"
        d = check_digit(s)
        if d is not None:
            return s + str(d)
        prefix8 += 1


A = make_orgnr(91234567)   # FJORDTECH AS: verified site, jobs, news, profiles
B = make_orgnr(92345678)   # NORDLYS HOLDING AS: no site; guessed domain shows another company
C = make_orgnr(93456789)   # BERGEN BYGG AS: declared site belongs to the parent (other org number)
D = make_orgnr(94567890)   # ROBOTSTOPP AS: robots.txt disallows everything
E = make_orgnr(95678901)   # KARI NORDMANN (ENK): sole proprietorship, no accounts in the open API
G = make_orgnr(96789012)   # BANKEN AS: accounts API refuses (500 with message), no roles
NF = make_orgnr(97890123)  # valid checksum, not registered anywhere
H = make_orgnr(93111111)   # GURIASEN HOLDING AS: email at the owner's employer (shared switchboard phone)
I = make_orgnr(93222222)   # LIABAKKEN BORETTSLAG: email at its housing-association manager, listed by name
J = make_orgnr(93333333)   # KVIST BYGG AS: email domain named after the company, no website in the register
K = make_orgnr(93444444)   # SKOGLY DATA AS: NAV ad under a longer business name, same org number
EMPLOYER = make_orgnr(93555555)
L = make_orgnr(93666666)   # TRESTEIN AS: email domain named after the company, but the domain is parked
M = make_orgnr(93777777)   # GLIMT ANALYSE AS: email at its accountant; own .com site shows org nr on /kontakt
SUB_A1 = make_orgnr(81234567)
SUB_A2 = make_orgnr(82345678)
PARENT_C = make_orgnr(91111111)
NAMESAKE = make_orgnr(98765432)
DAYS = lambda n: (datetime.now(timezone.utc) - timedelta(days=n)).strftime("%Y-%m-%d")  # noqa: E731
FUT = lambda n: (datetime.now(timezone.utc) + timedelta(days=n)).strftime("%Y-%m-%dT00:00:00+01:00")  # noqa: E731


def fmt(o: str) -> str:
    return f"{o[:3]}&nbsp;{o[3:6]}&nbsp;{o[6:]}"


def addr(street, pc, city, muni, mnr):
    return {"adresse": [street] if street else [], "postnummer": pc, "poststed": city, "kommune": muni,
            "kommunenummer": mnr, "land": "Norge", "landkode": "NO"}


def enhet(org, navn, form="AS", **kw):
    d = {"organisasjonsnummer": org, "navn": navn,
         "organisasjonsform": {"kode": form, "beskrivelse": {"AS": "Aksjeselskap", "ENK": "Enkeltpersonforetak"}[form]},
         "registreringsdatoEnhetsregisteret": "2014-01-27", "stiftelsesdato": "2014-01-10",
         "registrertIMvaregisteret": True, "registreringsdatoMerverdiavgiftsregisteret": "2014-01-01",
         "registrertIForetaksregisteret": True, "registrertIStiftelsesregisteret": False,
         "registrertIFrivillighetsregisteret": False,
         "naeringskode1": {"kode": "62.010", "beskrivelse": "Programmeringstjenester"},
         "harRegistrertAntallAnsatte": True, "antallAnsatte": 23,
         "registreringsdatoAntallAnsatteEnhetsregisteret": "2026-10-13",
         "forretningsadresse": addr("Bryggen 1", "5003", "BERGEN", "BERGEN", "4601"),
         "institusjonellSektorkode": {"kode": "2100", "beskrivelse": "Private aksjeselskaper mv."},
         "konkurs": False, "underAvvikling": False, "underTvangsavviklingEllerTvangsopplosning": False,
         "maalform": "Bokmål", "sisteInnsendteAarsregnskap": "2025", "erIKonsern": False, "historiskeNavn": [],
         "kapital": {"belop": 30000.0, "valuta": "NOK", "antallAksjer": 300, "type": "Aksjekapital",
                     "innfortDato": "2014-01-27"},
         "aktivitet": ["Utvikling og salg av programvare."], "vedtektsfestetFormaal": ["Programvareutvikling."]}
    d.update(kw)
    return {k: v for k, v in d.items() if v is not None}


ENHETER = {
    A: enhet(A, "FJORDTECH AS", hjemmeside="fjordtech.example", telefon="55 12 34 56",
             historiskeNavn=[{"navn": "FJORD SOFTWARE AS", "fraDato": "2014-01-27 10:00:00",
                              "tilDato": DAYS(200) + " 09:00:00"}]),
    B: enhet(B, "NORDLYS HOLDING AS", epostadresse="ola@gmail.com", antallAnsatte=None,
             harRegistrertAntallAnsatte=False, naeringskode1={"kode": "64.200", "beskrivelse": "Holdingselskaper"}),
    C: enhet(C, "BERGEN BYGG AS", hjemmeside="bergenbygg.example", erIKonsern=True),
    D: enhet(D, "ROBOTSTOPP AS", hjemmeside="https://robotstopp.example"),
    E: enhet(E, "KARI NORDMANN", form="ENK", antallAnsatte=None, harRegistrertAntallAnsatte=True,
             mobil="922 24 185", kapital=None, registrertIForetaksregisteret=False),
    G: enhet(G, "BANKEN AS", konkurs=True),
    H: enhet(H, "GURIASEN HOLDING AS", epostadresse="torkel@formuex.example", telefon="24 12 44 00",
             naeringskode1={"kode": "64.200", "beskrivelse": "Holdingselskaper"}),
    I: enhet(I, "LIABAKKEN BORETTSLAG", epostadresse="liabakken@obblx.example", telefon="35 02 90 10"),
    J: enhet(J, "KVIST BYGG AS", epostadresse="post@kvistbygg.example", telefon="33 44 55 66"),
    K: enhet(K, "SKOGLY DATA AS"),
    L: enhet(L, "TRESTEIN AS", epostadresse="post@trestein.example"),
    M: enhet(M, "GLIMT ANALYSE AS", epostadresse="glimt@regnskapx.example"),
}


def roles(org):
    if org == G:
        return {"rollegrupper": []}
    if org == E:
        return {"rollegrupper": [{"type": {"kode": "INNH", "beskrivelse": "Innehaver"}, "roller": [
            {"type": {"kode": "INNH", "beskrivelse": "Innehaver"},
             "person": {"fodselsdato": "1980-01-02", "navn": {"fornavn": "Kari", "etternavn": "Nordmann"}}}]}]}
    return {"rollegrupper": [
        {"type": {"kode": "DAGL", "beskrivelse": "Daglig leder/ adm.direktør"}, "roller": [
            {"type": {"kode": "DAGL", "beskrivelse": "Daglig leder/ adm.direktør"},
             "person": {"fodselsdato": "1975-05-17", "navn": {"fornavn": "Ola", "etternavn": "Hansen"}}}]},
        {"type": {"kode": "STYR", "beskrivelse": "Styre"}, "roller": [
            {"type": {"kode": "LEDE", "beskrivelse": "Styrets leder"},
             "person": {"fodselsdato": "1960-03-03", "navn": {"fornavn": "Eva", "mellomnavn": "M", "etternavn": "Berg"}}},
            {"type": {"kode": "MEDL", "beskrivelse": "Styremedlem"},
             "person": {"fodselsdato": "1970-07-07", "navn": {"fornavn": "Per", "etternavn": "Dahl"}}},
            {"type": {"kode": "MEDL", "beskrivelse": "Styremedlem"}, "fratraadt": True,
             "person": {"fodselsdato": "1950-01-01", "navn": {"fornavn": "Gammel", "etternavn": "Medlem"}}}]},
        {"type": {"kode": "REVI", "beskrivelse": "Revisor"}, "roller": [
            {"type": {"kode": "REVI", "beskrivelse": "Revisor"},
             "enhet": {"organisasjonsnummer": make_orgnr(99000000), "navn": ["REVISJON VEST AS"]}}]}]}


def subunits(org):
    items = []
    if org == A:
        items = [{"organisasjonsnummer": SUB_A1, "navn": "FJORDTECH AS", "overordnetEnhet": A,
                  "beliggenhetsadresse": addr("Bryggen 1", "5003", "BERGEN", "BERGEN", "4601"), "antallAnsatte": 15,
                  "harRegistrertAntallAnsatte": True, "oppstartsdato": "2014-01-15"},
                 {"organisasjonsnummer": SUB_A2, "navn": "FJORDTECH AVD OSLO", "overordnetEnhet": A,
                  "beliggenhetsadresse": addr("Karl Johans gate 2", "0154", "OSLO", "OSLO", "0301"),
                  "antallAnsatte": 8, "harRegistrertAntallAnsatte": True, "oppstartsdato": "2020-02-01"},
                 {"organisasjonsnummer": make_orgnr(83000000), "navn": "LEAK AS", "overordnetEnhet": NAMESAKE}]
    return {"_embedded": {"underenheter": items}, "page": {"totalElements": len(items), "size": 200, "number": 0}} if items \
        else {"page": {"totalElements": 0, "size": 200, "number": 0}}


def period(y, rev, res, eq, typ="SELSKAP", org=A):
    return {"id": y, "regnskapstype": typ, "virksomhet": {"organisasjonsnummer": org, "organisasjonsform": "AS"},
            "regnskapsperiode": {"fraDato": f"{y}-01-01", "tilDato": f"{y}-12-31"}, "valuta": "NOK",
            "avviklingsregnskap": False, "revisjon": {"fravalgRevisjon": False},
            "regnkapsprinsipper": {"smaaForetak": True, "regnskapsregler": "regnskapslovenAlminneligRegler"},
            "egenkapitalGjeld": {"egenkapital": {"sumEgenkapital": eq}, "gjeldOversikt": {"sumGjeld": 400000.0}},
            "eiendeler": {"sumEiendeler": eq + 400000.0},
            "resultatregnskapResultat": {"ordinaertResultatFoerSkattekostnad": res * 1.2, "aarsresultat": res,
                                         "driftsresultat": {"driftsresultat": res * 1.3,
                                                            "driftsinntekter": {"sumDriftsinntekter": rev},
                                                            "driftskostnad": {"loennskostnad": 9000000.0}}}}


ACCOUNTS = {
    A: [period(2023, 10000000.0, 500000.0, 1000000.0), period(2024, 12000000.0, 800000.0, 1500000.0),
        period(2025, 15000000.0, 1200000.0, 2400000.0), period(2025, 99000000.0, 1.0, 2.0, typ="KONSERN")],
    B: [period(2025, 0.0, -20000.0, -300000.0, org=B)],
    C: [period(2025, 5000000.0, 100000.0, 200000.0, org=C)],
    D: [period(2025, 2000000.0, 10000.0, 50000.0, org=D)],
}


HOME_A = f"""<!doctype html><html lang="nb"><head><meta charset="utf-8"><title>Fjordtech – programvare fra Bergen</title>
<meta name="description" content="Fjordtech lager skreddersydd programvare for havbruk og energi.">
<meta property="og:site_name" content="Fjordtech">
<link rel="alternate" type="application/rss+xml" href="/feed.xml">
<script type="application/ld+json">{{"@context":"https://schema.org","@type":"Organization","name":"Fjordtech",
"sameAs":["https://www.youtube.com/@fjordtech"],"address":{{"@type":"PostalAddress","streetAddress":"Bryggen 1",
"postalCode":"5003","addressLocality":"Bergen","addressCountry":"NO"}}}}</script></head>
<body><nav><a href="/om-oss">Om oss</a> <a href="/karriere">Ledige stillinger</a> <a href="/nyheter">Nyheter</a></nav>
<main><h1>Programvare for havet</h1></main>
<footer>Fjordtech AS · Org.nr: {fmt(A)} · <a href="mailto:post@fjordtech.example">post@fjordtech.example</a>
<a href="tel:+4755123456">55 12 34 56</a>
<a href="https://www.linkedin.com/company/fjordtech/">LinkedIn</a>
<a href="https://www.facebook.com/fjordtech">Facebook</a>
<a href="https://www.facebook.com/sharer/sharer.php?u=x">Del</a>
<a href="https://twitter.com/intent/tweet?text=x">Tweet</a></footer></body></html>"""

CAREERS_A = """<html><head><title>Karriere – Fjordtech</title>
<script type="application/ld+json">[{"@context":"https://schema.org","@type":"JobPosting","title":"Backend-utvikler",
"datePosted":"%s","validThrough":"%s","hiringOrganization":{"@type":"Organization","name":"Fjordtech AS"},
"jobLocation":{"@type":"Place","address":{"addressLocality":"Bergen"}},"url":"https://www.fjordtech.example/karriere/backend"},
{"@context":"https://schema.org","@type":"JobPosting","title":"Lagerarbeider (vikar)","datePosted":"%s",
"hiringOrganization":{"@type":"Organization","name":"Other Staffing AS"}}]</script></head>
<body><h1>Jobb hos oss</h1><ul>
<li><a href="https://fjordtech.teamtailor.com/jobs/123456-product-designer">Product Designer</a></li>
<li><a href="/karriere">Karriere</a></li></ul></body></html>""" % (DAYS(10), DAYS(-30), DAYS(5))

FEED_A = f"""<?xml version="1.0"?><rss version="2.0"><channel><title>Fjordtech nyheter</title>
<item><title>Fjordtech vinner kontrakt med Havbruk Nord</title><link>https://www.fjordtech.example/nyheter/kontrakt</link>
<pubDate>{datetime.now(timezone.utc) - timedelta(days=20):%a, %d %b %Y %H:%M:%S +0000}</pubDate></item>
<item><title>Ny daglig leder i Fjordtech</title><link>https://www.fjordtech.example/nyheter/ny-leder</link>
<pubDate>{datetime.now(timezone.utc) - timedelta(days=120):%a, %d %b %Y %H:%M:%S +0000}</pubDate></item>
<item><title>Gammel nyhet</title><link>https://www.fjordtech.example/nyheter/gammel</link>
<pubDate>Mon, 01 Jan 2018 10:00:00 +0000</pubDate></item></channel></rss>"""

NEWS_A = f"""<html><head><title>Nyheter</title></head><body>
<article><time datetime="{DAYS(45)}">{DAYS(45)}</time><h2><a href="/nyheter/sertifisering">Fjordtech er ISO 27001-sertifisert</a></h2></article>
</body></html>"""

HOME_C = f"""<html><head><title>Bergen Bygg Gruppen</title></head><body><h1>Bergen Bygg Gruppen</h1>
<p>Vi bygger Vestlandet.</p><footer>Bergen Bygg Gruppen AS – org.nr. {PARENT_C} MVA</footer></body></html>"""
HOME_GUESS_B = f"""<html><head><title>Nordlys Holding</title></head><body>Nordlys Holding AS, Tromsø.
Organisasjonsnummer: {NAMESAKE}</body></html>"""
HOME_FORMUE = """<html><head><title>Formuex – formuesforvaltning</title></head><body><h1>Formuex</h1>
<p>Ring oss på 24 12 44 00. Vi forvalter formue for familier, selskaper og stiftelser, blant annet
Guriasen Holding og andre.</p><a href="/karriere">Karriere</a></body></html>"""
CAREERS_FORMUE = '''<html><head><title>Karriere</title><script type="application/ld+json">{"@type":"JobPosting",
"title":"Senior Controller","datePosted":"2026-10-01","hiringOrganization":{"name":"Formuex"}}</script></head>
<body>Jobb hos Formuex</body></html>'''
HOME_OBBL = """<html><head><title>Obblx Boligbyggelag</title></head><body><h1>Obblx Boligbyggelag</h1>
<p>Våre borettslag: Liabakken Borettslag, Granlia Borettslag, Solsiden Borettslag.</p>
<p>Sentralbord 35 02 90 10</p></body></html>"""
HOME_KVIST = """<html><head><title>Kvist Bygg – tømrer i Tønsberg</title></head><body><h1>Kvist Bygg AS</h1>
<p>Ring 33 44 55 66</p></body></html>"""
HOME_D = "<html><head><title>Robotstopp</title></head><body>ROBOTSTOPP AS org.nr " + D + "</body></html>"


def nav_ad(uuid, title, employer, orgnr, homepage=None, status="ACTIVE"):
    return {"uuid": uuid, "status": status, "sistEndret": DAYS(3) + "T10:00:00Z", "ad_content": {
        "uuid": uuid, "published": DAYS(12) + "T08:00:00+01:00", "expires": FUT(20), "updated": DAYS(3),
        "workLocations": [{"city": "BERGEN", "municipal": "BERGEN", "country": "NORGE"}],
        "contactList": [{"name": "Hemmelig Kontakt", "email": "hr@x.example", "phone": "99999999"}],
        "title": title, "description": "<p>Stilling</p>", "applicationDue": "Snarest", "jobtitle": title,
        "link": f"https://arbeidsplassen.nav.no/stillinger/stilling/{uuid}",
        "employer": {"name": employer, "orgnr": orgnr, "homepage": homepage}, "positioncount": "1"}}


NAV_ADS = {
    "11111111-1111-1111-1111-111111111111": nav_ad("11111111-1111-1111-1111-111111111111", "Senior utvikler",
                                                   "FJORDTECH AS", A, "https://www.fjordtech.example"),
    "22222222-2222-2222-2222-222222222222": nav_ad("22222222-2222-2222-2222-222222222222", "Konsulent Oslo",
                                                   "FJORDTECH AVD OSLO", SUB_A2),
    "55555555-5555-5555-5555-555555555555": nav_ad("55555555-5555-5555-5555-555555555555", "Systemutvikler",
                                                   "Skogly Data Avdeling Drammen", K),
    "33333333-3333-3333-3333-333333333333": nav_ad("33333333-3333-3333-3333-333333333333", "Snekker",
                                                   "FJORDTECH AS", NAMESAKE),
}


def feed_page(items, next_id):
    return {"version": "1.0", "title": "feed", "id": "p", "next_id": next_id,
            "next_url": f"/api/v1/feed/{next_id}" if next_id else None, "items": items}


def feed_item(uuid, business, status="ACTIVE", title="t"):
    return {"id": uuid, "url": f"/api/v1/feedentry/{uuid}", "title": title, "date_modified": DAYS(3) + "T10:00:00Z",
            "_feed_entry": {"uuid": uuid, "status": status, "title": title, "businessName": business,
                            "municipal": "BERGEN", "sistEndret": DAYS(3) + "T10:00:00Z"}}


class FakeInternet:
    """Callable with the Http opener signature: (url, headers, timeout, max_bytes) -> (status, headers, body)."""

    def __init__(self):
        self.calls: list[str] = []
        self.fail: set[str] = set()  # substrings of URLs that should fail (network error)
        self.enheter = json.loads(json.dumps(ENHETER))
        self.roles_override: dict = {}
        self.feed_pages = {
            "first": feed_page([feed_item("11111111-1111-1111-1111-111111111111", "Fjordtech AS"),
                                feed_item("33333333-3333-3333-3333-333333333333", "FJORDTECH AS")], "p2"),
            "p2": feed_page([feed_item("22222222-2222-2222-2222-222222222222", "Fjordtech avd. Oslo"),
                             feed_item("55555555-5555-5555-5555-555555555555", "Skogly Data Avdeling Drammen"),
                             feed_item("44444444-4444-4444-4444-444444444444", "Annen Bedrift AS")], None),
        }

    @staticmethod
    def j(obj, status=200):
        return status, {"Content-Type": "application/json"}, json.dumps(obj).encode()

    @staticmethod
    def h(text, status=200, ctype="text/html; charset=utf-8"):
        return status, {"Content-Type": ctype}, text.encode("utf-8")

    def __call__(self, url, headers, timeout, max_bytes):
        self.calls.append(url)
        for f in self.fail:
            if f in url:
                raise OSError("simulated network failure")
        p = urlsplit(url)
        q = {k: v[0] for k, v in parse_qs(p.query).items()}
        host, path = p.hostname, p.path
        if host == "data.brreg.no":
            return self.brreg(path, q)
        if host == "pam-stilling-feed.nav.no":
            return self.nav(path, q, headers)
        return self.web(url, host, path)

    def brreg(self, path, q):
        api = "/enhetsregisteret/api"
        if path == f"{api}/enheter" and "organisasjonsnummer" in q:
            want = q["organisasjonsnummer"].split(",")
            items = [self.enheter[o] for o in want if o in self.enheter]
            return self.j({"_embedded": {"enheter": items}, "page": {"totalElements": len(items)}} if items
                          else {"page": {"totalElements": 0}})
        if path == f"{api}/underenheter" and "organisasjonsnummer" in q:
            want = q["organisasjonsnummer"].split(",")
            items = [u for o in (A,) for u in subunits(o)["_embedded"]["underenheter"] if u["organisasjonsnummer"] in want]
            return self.j({"_embedded": {"underenheter": items}} if items else {"page": {"totalElements": 0}})
        if path == f"{api}/underenheter" and "overordnetEnhet" in q:
            return self.j(subunits(q["overordnetEnhet"]))
        m = re.fullmatch(api + r"/(enheter|underenheter)/(\d{9})", path)
        if m:
            o = m.group(2)
            if m.group(1) == "enheter" and o in self.enheter:
                return self.j(self.enheter[o])
            return self.j({"feilmelding": "Ingen enhet"}, 404)
        m = re.fullmatch(api + r"/enheter/(\d{9})/roller", path)
        if m:
            o = m.group(1)
            return self.j(self.roles_override.get(o, roles(o)))
        if path == f"{api}/oppdateringer/enheter":
            want = q["organisasjonsnummer"].split(",")
            evs = []
            if A in want:
                evs += [{"oppdateringsid": 1, "organisasjonsnummer": A, "dato": DAYS(300) + "T10:00:00.000Z",
                         "endringstype": "Endring"},
                        {"oppdateringsid": 2, "organisasjonsnummer": A, "dato": DAYS(200) + "T10:00:00.000Z",
                         "endringstype": "Endring", "endringer": [{"op": "replace", "path": "/navn", "value": "FJORDTECH AS"}]},
                        {"oppdateringsid": 3, "organisasjonsnummer": A, "dato": DAYS(60) + "T10:00:00.000Z",
                         "endringstype": "Endring", "endringer": [{"op": "replace", "path": "/antallAnsatte", "value": 22},
                                                                  {"op": "replace", "path": "/registreringsdatoAntallAnsatteNAVAaregisteret", "value": "x"}]},
                        {"oppdateringsid": 4, "organisasjonsnummer": A, "dato": DAYS(30) + "T10:00:00.000Z",
                         "endringstype": "Endring", "endringer": [{"op": "replace", "path": "/antallAnsatte", "value": 23}]},
                        {"oppdateringsid": 5, "organisasjonsnummer": A, "dato": DAYS(150) + "T10:00:00.000Z",
                         "endringstype": "Endring", "endringer": [{"op": "replace", "path": "/sisteInnsendteAarsregnskap", "value": "2025"}]}]
            evs.append({"oppdateringsid": 9, "organisasjonsnummer": NAMESAKE, "dato": DAYS(5) + "T10:00:00.000Z",
                        "endringstype": "Endring", "endringer": [{"op": "replace", "path": "/navn", "value": "X"}]})
            return self.j({"_embedded": {"oppdaterteEnheter": evs}, "page": {"totalPages": 1, "totalElements": len(evs)}})
        if path == f"{api}/oppdateringer/roller":
            want = q["organisasjonsnummer"].split(",")
            out = []
            if A in want:
                out.append({"id": "77", "time": DAYS(90) + "T06:00:00.000Z", "type": "no.brreg.enhetsregisteret.rolle.oppdatert",
                            "source": f"https://data.brreg.no/enhetsregisteret/api/enheter/{A}/roller",
                            "data": {"organisasjonsnummer": A}, "specversion": "1.0"})
            return self.j(out)
        m = re.fullmatch(r"/regnskapsregisteret/regnskap/(\d{9})", path)
        if m:
            o = m.group(1)
            if o == G:
                return self.j({"message": "Regnskapet inneholder en oppstillingsplan som ikke støttes"}, 500)
            if o in ACCOUNTS:
                return self.j(ACCOUNTS[o])
            return self.j({"message": "Not found"}, 404)
        return self.j({"error": "unknown"}, 404)

    def nav(self, path, q, headers):
        if path == "/api/publicToken":
            return 200, {"Content-Type": "text/plain"}, b"Current public token for Nav Job Vacancy Feed:\nabcdefghij.klmnopqrst.uvwxyz12345"
        assert headers.get("Authorization", "").startswith("Bearer "), "feed called without token"
        if path == "/api/v1/feed":
            return self.j(self.feed_pages["first"])
        m = re.fullmatch(r"/api/v1/feed/(\w+)", path)
        if m:
            return self.j(self.feed_pages[m.group(1)])
        m = re.fullmatch(r"/api/v1/feedentry/([\w-]+)", path)
        if m and m.group(1) in NAV_ADS:
            return self.j(NAV_ADS[m.group(1)])
        return self.j({}, 404)

    def web(self, url, host, path):
        if host is None:
            raise OSError("bad url")
        if host in ("fjordtech.example",):
            return 301, {"Location": "https://www.fjordtech.example" + path}, b""
        if host == "www.fjordtech.example":
            pages = {"/robots.txt": ("User-agent: *\nDisallow: /admin\n", "text/plain"),
                     "/": (HOME_A, "text/html"), "/karriere": (CAREERS_A, "text/html"),
                     "/feed.xml": (FEED_A, "application/rss+xml"), "/nyheter": (NEWS_A, "text/html"),
                     "/om-oss": ("<html><body>Om Fjordtech</body></html>", "text/html")}
            if path in pages:
                body, ct = pages[path]
                return self.h(body, ctype=ct + "; charset=utf-8")
            return self.h("not found", 404)
        if host == "fjordtech.teamtailor.com":
            return self.h("User-agent: *\nAllow: /\n", ctype="text/plain") if path == "/robots.txt" else self.h("<html></html>")
        if host == "bergenbygg.example":
            if path == "/robots.txt":
                return self.h("", 404)
            return self.h(HOME_C)
        if host == "robotstopp.example":
            if path == "/robots.txt":
                return self.h("User-agent: *\nDisallow: /\n", ctype="text/plain")
            return self.h(HOME_D)
        if host == "formuex.example":
            if path == "/robots.txt":
                return self.h("", 404)
            return self.h(CAREERS_FORMUE if path == "/karriere" else HOME_FORMUE)
        if host == "obblx.example":
            return self.h("", 404) if path == "/robots.txt" else self.h(HOME_OBBL)
        if host == "regnskapx.example":
            return self.h("", 404) if path == "/robots.txt" else self.h(
                f"<html><head><title>Regnskap X</title></head><body>Regnskap X AS, org.nr. {fmt(EMPLOYER)}. "
                "Vi fører regnskap for Glimt Analyse og mange andre.</body></html>")
        if host in ("glimtanalyse.com", "www.glimtanalyse.com"):
            if path == "/":
                return self.h("<html><head><title>Glimt</title></head><body><h1>Data og analyse</h1></body></html>")
            if path == "/kontakt":
                return self.h(f"<html><body>Glimt Analyse AS · Org.nr. {fmt(M)} · Oslo</body></html>")
            return self.h("not found", 404)
        if host == "trestein.example":
            return self.h("", 404) if path == "/robots.txt" else self.h(
                "<html><head><title>trestein.example is parked</title></head><body>trestein.example</body></html>")
        if host == "kvistbygg.example":
            return self.h("", 404) if path == "/robots.txt" else self.h(HOME_KVIST)
        if host in ("nordlysholding.no", "www.nordlysholding.no"):
            return self.h("", 404) if path == "/robots.txt" else self.h(HOME_GUESS_B)
        raise OSError(f"Name or service not known: {host}")


def batch_lines():
    return [{"organisation_number": o} for o in (A, B, C, D, E, G, NF, H, I, J, K, L, M)] + \
        [{"organisation_number": "123456789"}, {"organisation_number": A}]
