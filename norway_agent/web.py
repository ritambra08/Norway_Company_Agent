"""Official website verification and company-owned pages (profiles, careers, news).

Identity gate (nothing is published from a site that fails it):
  * the site shows this company's organisation number (homepage, or a contact/about/privacy page),
    and the page is not a directory listing many org numbers; or
  * the site was declared for this company by an official source (Enhetsregisteret ``hjemmeside``,
    or the employer homepage on a NAV ad already tied to this org number, or the registered email
    domain) AND the site names the company (all distinctive name tokens) or shows its registered
    phone or email.
A guessed ``<name>.no`` needs the org number, or name plus registered phone/email.
A site that shows a *different* org number and not ours is never merged: declared sites become
``ambiguous`` (likely a parent, group or sister company), guessed sites are discarded.

Every fetch honours robots.txt for our user agent. Only static HTML is read (no JavaScript).
"""

from __future__ import annotations

import re
import socket
import os
import urllib.robotparser
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutTimeout
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlsplit, urlunsplit
import xml.etree.ElementTree as ET

from . import orgnr as orgnr_mod
from .config import Config
from .htmlparse import Doc, parse, types_of
from .http import BudgetExceeded, Http, Response, Scope
from .names import core_tokens, domain_candidates, fold, page_names_company
from .snapshots import SnapshotStore
from .state import CompanyState, SourceStatus

FREE_MAIL = {"gmail.com", "hotmail.com", "hotmail.no", "outlook.com", "live.com", "live.no", "yahoo.com",
             "yahoo.no", "icloud.com", "me.com", "msn.com", "online.no", "getmail.no", "frisurf.no",
             "broadpark.no", "altibox.no", "lyse.net", "start.no", "c2i.net", "tele2.no", "chello.no",
             "epost.no", "mail.com", "protonmail.com", "proton.me", "aol.com", "gmx.com", "gmx.net",
             "bluezone.no", "tiscali.no", "sol.no", "loqal.no", "haugnett.no", "enivest.net", "kvinnherad.net",
             "netcom.no", "hotmail.co.uk", "googlemail.com", "ymail.com", "outlook.no", "vikenfiber.no"}

SOCIAL = {
    "linkedin": (r"(?:^|\.)linkedin\.com$", r"^/(company|school|showcase)/[^/?#]+"),
    "facebook": (r"(?:^|\.)(facebook|fb)\.com$", r"^/(?!sharer|share|dialog|plugins|tr\b|login|groups/?$|events/?$)"
                                                  r"(profile\.php\?id=\d+|pages/[^/]+/\d+|[A-Za-z0-9.\-]{3,})/?$"),
    "instagram": (r"(?:^|\.)instagram\.com$", r"^/(?!p/|explore|accounts|reel/)[A-Za-z0-9._]{2,}/?$"),
    "x": (r"(?:^|\.)(twitter|x)\.com$", r"^/(?!intent|share|home|search|hashtag|i/)[A-Za-z0-9_]{2,15}/?$"),
    "youtube": (r"(?:^|\.)youtube\.com$", r"^/(channel/[A-Za-z0-9_\-]+|c/[^/]+|user/[^/]+|@[^/]+)/?$"),
    "tiktok": (r"(?:^|\.)tiktok\.com$", r"^/@[A-Za-z0-9._]+/?$"),
}
CAREER_WORDS = re.compile(r"karriere|ledige[\s_-]*stillinger|ledig[\s_-]*stilling|\bstillinger\b|jobb\s*hos|"
                          r"jobbe\s*(hos|i|med)|bli\s*(en\s*av\s*oss|med)|\bcareers?\b|\bjobs\b|vacanc|"
                          r"join[\s_-]*(us|the\s*team)|work[\s_-]*with[\s_-]*us|\bjobb\b|rekrutter", re.I)
CONTACT_WORDS = re.compile(r"kontakt|contact|om[\s_-]*oss|about|personvern|privacy|impressum|firma|"
                           r"selskapet|company", re.I)
NEWS_WORDS = re.compile(r"\bnyhet(er)?\b|\baktuelt\b|\baktuell\b|\bnews\b|\bpresse(meldinger|rom)?\b|\bpress\b|"
                        r"\bblogg?\b|\bartikler\b|\binnsikt\b|\binsights\b|siste[\s_-]*nytt|\bnytt\s+fra\b|"
                        r"/(nyheter|aktuelt|news|presse|blogg?|artikler)/?$", re.I)
CONTACT_TEXT = re.compile(r"^\s*(kontakt(\s*oss)?|kontakt\s*informasjon|contact(\s*us)?|om\s*oss|om\s+\w+|about(\s*us)?|"
                          r"personvern\w*|privacy(\s*policy)?|personvernerkl\w*|impressum|firma\w*|selskapet|"
                          r"company(\s*info\w*)?|kontakter|adresser?)\s*$", re.I)
CONTACT_PATH = re.compile(r"/(kontakt(-oss)?|contact(-us)?|om-oss|om|about(-us)?|personvern\w*|privacy(-policy)?|"
                          r"impressum|firma\w*|kontakter|selskapet|company)/?$", re.I)
NO_VACANCY = re.compile(r"ingen\s+ledige\s+stillinger|har\s+(for\s+tiden\s+)?ingen\s+ledige|ingen\s+utlyste\s+"
                        r"stillinger|ingen\s+aktive\s+stillinger|no\s+(open|current)\s+(positions|vacancies|"
                        r"openings|jobs)|there\s+are\s+(currently\s+)?no\s+(open\s+)?(positions|vacancies)", re.I)
PARKED = re.compile(r"\bis\s+parked\b|\bparked\s+(free|domain|by)\b|domain\s+parking|parkingcrew|sedoparking|"
                    r"\bbodis\b|may\s+be\s+for\s+sale|domenet\s+(er\s+)?til\s+salgs|"
                    r"domain(et)?\s+(is|er)\s+(for\s+sale|til\s+salgs)|buy\s+this\s+domain|this\s+domain\s+"
                    r"(is\s+)?(parked|registered)|domenet\s+er\s+registrert|parkert\s+domene|"
                    r"under\s+construction|kommer\s+snart|coming\s+soon|website\s+is\s+(being\s+)?"
                    r"(built|under)|account\s+suspended|default\s+web\s+site\s+page|it\s+works!", re.I)
SEP = r"(?:\s|&nbsp;|&#160;|\xa0|\.|-)?"
ORG_LABEL = re.compile(r"(org(anisasjons)?\.?\s*(nr|nummer|no|number)|foretaksregisteret|\bmva\b|\bvat\b|"
                       r"orgnr|org\.?-?nr|org\s*id|business\s*(id|reg))", re.I)
LABEL_BEFORE = re.compile(r"(?:" + ORG_LABEL.pattern + r")(?:\s|&nbsp;|&#160;|<[^>]{0,40}>|[:.#\-–])*$", re.I)
ANY_ORG = re.compile(r"(?<!\d)(\d{3})" + SEP + r"(\d{3})" + SEP + r"(\d{3})(?!\d)")

ATS_HOSTS = re.compile(r"(teamtailor\.com|jobylon\.com|webcruiter\.(no|com)|recman\.(no|io)|hrmanager\.(no|com)|"
                       r"easycruit\.com|reachmee\.com|jobbnorge\.no|smartrecruiters\.com|lever\.co|"
                       r"greenhouse\.io|varbi\.com|emply\.(com|net)|workday|cvpartner|talentech|"
                       r"arbeidsplassen\.nav\.no|finn\.no)$", re.I)
JOB_LINK = re.compile(
    r"(teamtailor\.com/(?:[a-z]{2}(?:-[a-z]{2})?/)?jobs/\d+|jobylon\.com/(?:[a-z]{2}/)?jobs/\d+|"
    r"webcruiter\.(?:no|com)/.*(?:advertid|AdvertId|VacancyNo|advert)=?\d+|recman\.(?:no|io)/job\.php\?job_id=\d+|"
    r"hrmanager\.(?:no|com)/.*(?:Vacancy|vacancy|ad\.aspx\?)\S*\d+|easycruit\.com/vacancy/\d+|"
    r"reachmee\.com/.*(?:job|ad)[^\s]*id=\d+|jobbnorge\.no/(?:\w+/)?(?:available-jobs|ledige-stillinger)/(?:job|stilling)/\d+|"
    r"jobs\.smartrecruiters\.com/[^/]+/\d+|jobs\.lever\.co/[^/]+/[0-9a-f\-]{36}|"
    r"boards\.greenhouse\.io/[^/]+/jobs/\d+|arbeidsplassen\.nav\.no/stillinger/stilling/[0-9a-f\-]{36}|"
    r"finn\.no/(?:job/[^\s]*finnkode=\d+|\d{6,}))", re.I)
# Public job boards: a page there shows "similar jobs" from other employers, so it is never read as a
# company's careers page (NAV ads are collected by org number from the NAV feed instead).
JOB_BOARDS = re.compile(r"(^|\.)(arbeidsplassen\.nav\.no|nav\.no|finn\.no|jobbnorge\.no|karrierestart\.no|"
                        r"indeed\.com|no\.indeed\.com|linkedin\.com|stepstone\.\w+|jobindex\.\w+)$", re.I)
GENERIC_LINK = re.compile(r"^\s*(les\s+mer.*|søk\s+her.*|søk\s+nå|se\s+stilling(en)?|mer\s+info.*|read\s+more.*|"
                          r"apply.*|show\s+more|vis\s+mer|klikk\s+her.*|her)\s*$", re.I)
# Link texts on careers pages that talk ABOUT working there (or are navigation), not posting titles.
CAREER_TALK = re.compile(r"ledige\s+stillinger|ledig\s+stilling(?!\s+som)|jobbe\s+(hos|i|med|for)\b|jobb\s+hos|"
                         r"karriere|bli\s+(en\s+av\s+oss|med\s+(oss|på\s+laget))|work\s+(with|at|for)\s+us|"
                         r"\bcareers?\b|join\s+(us|our)|see\s+(a\s+list|all)|hopp\s+til|skip\s+to|hovedinnhold|"
                         r"main\s+content|til\s+innhold|^\s*(meny|menu|logg\s+inn|log\s+in|sign\s+in)\s*$", re.I)
# A link on the company's own careers page counts as a posting only if its text reads like a job title.
JOB_TITLE_HINT = re.compile(
    r"\bsøk(er|es)\b|\b\w*(fører|sjåfør|leder|ingeniør|tekniker|konsulent|utvikler|medarbeider|rådgiver|selger|"
    r"lærling|operatør|montør|arbeider|elektriker|snekker|tømrer|rørlegger|sykepleier|assistent|koordinator|"
    r"ansvarlig|sjef|spesialist|analytiker|designer|manager|engineer|developer|specialist|coordinator|assistant|"
    r"technician|officer|director|consultant|advisor|analyst|architect|intern|trainee|vikar|vikariat|lærer|pedagog|"
    r"kokk|servitør|frisør|mekaniker|sveiser|maskinist|revisor|advokat|jurist|økonom|controller|terapeut|lege|"
    r"sekretær|resepsjonist|vekter|renholder|programmerer|planlegger|fagarbeider|elev|representative|associate)"
    r"(e|en|er|ne|s)?\b|\bheltid\b|\bdeltid\b|fast\s+stilling|sommerjobb|ledig\s+stilling\s+som|\b100\s*%|"
    r"\((m/w/d|m/f/d|f/m/d)\)", re.I)
TRAILING_CTA = re.compile(r"\s*[-–|·:]?\s*(se\s+stilling(en)?|les\s+mer|søk\s+nå|søk\s+her|apply(\s+now)?|"
                          r"read\s+more|mer\s+info)\s*$", re.I)
# Platform, website-builder and legal-page handles that are never a company's own profile.
SOCIAL_NOT_A_COMPANY = {
    "wix", "wixcom", "wixstudio", "squarespace", "wordpress", "wordpresscom", "wordpressdotcom", "shopify",
    "weebly", "godaddy", "jimdo", "webflow", "elementor", "hubspot", "mailchimp", "zyro", "hostinger", "one",
    "onecom", "domeneshop", "facebook", "instagram", "meta", "twitter", "x", "linkedin", "youtube", "google",
    "tiktok", "apple", "microsoft", "policyphp", "policy", "policies", "privacy", "legal", "help", "terms", "tos",
    "about", "business", "ads", "watch", "pages", "home", "homephp", "login", "signup", "settings", "profilephp",
}
# A country section of a multinational site (/no-no, /nb-no, /en-gb): its jobs, news and profiles are the group's.
LOCALE_PATH = re.compile(r"^/[a-z]{2}[-_][a-z]{2}(/|$)", re.I)
# Norwegian sites must show the org number (ehandelsloven § 8); it is usually on one of these pages.
PROBE_PATHS = ("/kontakt", "/om-oss", "/personvern")
SITE_JOB_PATH = re.compile(r"/(stilling|stillinger|ledige-stillinger|jobb|jobber|jobs|job|karriere|career|careers|"
                           r"vacancies|vacancy)/[^/?#]{4,}", re.I)

MONTHS = {"jan": 1, "januar": 1, "january": 1, "feb": 2, "februar": 2, "february": 2, "mar": 3, "mars": 3,
          "march": 3, "apr": 4, "april": 4, "mai": 5, "may": 5, "jun": 6, "juni": 6, "june": 6, "jul": 7,
          "juli": 7, "july": 7, "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "okt": 10,
          "oktober": 10, "oct": 10, "october": 10, "nov": 11, "november": 11, "des": 12, "desember": 12,
          "dec": 12, "december": 12}


# ---------------------------------------------------------------------------------------------
# helpers

def registrable(host: str) -> str:
    host = (host or "").lower().strip(".")
    if host.startswith("www."):
        host = host[4:]
    parts = host.split(".")
    if len(parts) >= 3 and parts[-2] in {"co", "com", "org", "net", "ac", "gov"} and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def normalize_site(raw: str) -> str | None:
    raw = (raw or "").strip().strip("/")
    if not raw or " " in raw or "@" in raw:
        return None
    if not re.match(r"^https?://", raw, re.I):
        raw = "https://" + raw
    parts = urlsplit(raw)
    if not parts.hostname or "." not in parts.hostname:
        return None
    return urlunsplit(("https", parts.hostname.lower(), parts.path or "/", "", ""))


def orgnr_pattern(org: str) -> re.Pattern:
    return re.compile(r"(?<!\d)" + org[:3] + SEP + org[3:6] + SEP + org[6:] + r"(?!\d)")


def labelled_orgnrs(raw: str) -> set[str]:
    """Valid org numbers that a page presents as org numbers (label within 40 chars)."""
    out = set()
    for m in ANY_ORG.finditer(raw):
        num = "".join(m.groups())
        if not orgnr_mod.is_valid(num):
            continue
        before = raw[max(0, m.start() - 40):m.start()]
        after = raw[m.end():m.end() + 12]
        if LABEL_BEFORE.search(before) or re.search(r"^\s*mva\b", after, re.I) or re.search(r"\bNO\s*$", before):
            out.add(num)
    return out


def span(raw: str, start: int, end: int, pad: int = 60) -> str:
    return raw[max(0, start - pad):min(len(raw), end + pad)]


def name_span(raw: str, name: str) -> str:
    """Raw-HTML excerpt showing the company's required name words (title first), for evidence spans."""
    from .names import fold, required_tokens
    req = required_tokens(name)
    m = re.search(r"<title[^>]*>(.*?)</title>", raw, re.I | re.S)
    if m and req and all(f" {t} " in f" {fold(m.group(1))} " for t in req):
        return raw[m.start():m.end()][:300]
    low = raw.lower()
    first = req[0].lower() if req else ""
    i = low.find(first) if first else -1
    while i >= 0:
        window = raw[max(0, i - 120):i + 200]
        if all(f" {t} " in f" {fold(window)} " for t in req):
            return window
        i = low.find(first, i + 1)
    return (m.group(0)[:300] if m else raw[:200])


def _date(s: str) -> str | None:
    """ISO date from ISO strings, RFC 822 dates or Norwegian/English textual dates."""
    s = (s or "").strip()
    if not s:
        return None
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    try:
        d = parsedate_to_datetime(s)
        if d:
            return d.date().isoformat()
    except (TypeError, ValueError, IndexError):
        pass
    m = re.search(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", s)
    if m:
        try:
            return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1))).date().isoformat()
        except ValueError:
            return None
    m = re.search(r"(\d{1,2})\.?\s+([A-Za-zæøå]+)\.?\s+(\d{4})", s)
    if m and m.group(2).lower() in MONTHS:
        try:
            return datetime(int(m.group(3)), MONTHS[m.group(2).lower()], int(m.group(1))).date().isoformat()
        except ValueError:
            return None
    m = re.search(r"([A-Za-z]+)\s+(\d{1,2}),\s+(\d{4})", s)
    if m and m.group(1).lower() in MONTHS:
        try:
            return datetime(int(m.group(3)), MONTHS[m.group(1).lower()], int(m.group(2))).date().isoformat()
        except ValueError:
            return None
    return None


def _in_window(d: str | None, days: int) -> bool:
    if not d:
        return False
    try:
        dt = datetime.fromisoformat(d).replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    now = datetime.now(timezone.utc)
    return now - timedelta(days=days) <= dt <= now + timedelta(days=2)


def _resolves(host: str, timeout: float = 3.0) -> bool:
    if os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy"):
        return True  # behind a proxy local DNS may be unavailable; let the proxy decide
    with ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(socket.getaddrinfo, host, 443)
        try:
            return bool(fut.result(timeout=timeout))
        except (FutTimeout, OSError, UnicodeError):
            return False


# ---------------------------------------------------------------------------------------------
# crawling one site

class SiteCrawler:
    def __init__(self, http: Http, cfg: Config, snaps: SnapshotStore, robots_cache: dict, scope: Scope):
        self.http, self.cfg, self.snaps, self.robots_cache, self.scope = http, cfg, snaps, robots_cache, scope
        self.pages: list[tuple[Response, Doc | None]] = []
        self.budget = cfg.web_pages_per_company

    def _robots(self, origin: str):
        if origin in self.robots_cache:
            return self.robots_cache[origin]
        rp = urllib.robotparser.RobotFileParser()
        r = self._get(origin + "/robots.txt", count_page=False)
        if r is None:
            raise BudgetExceeded("per-company page budget reached")
        if r.status in (401, 403):
            rp.parse(["User-agent: *", "Disallow: /"])
        elif r.ok and "html" not in r.content_type:
            rp.parse(r.text().splitlines())
        elif r.status >= 500 or r.status == 0:
            rp = "unreachable"  # RFC 9309: do not crawl; reported as a failed fetch, not a block
        else:
            rp.parse([])  # 404 etc => no restrictions
        self.robots_cache[origin] = rp
        return rp

    def _get(self, url: str, count_page: bool = True) -> Response | None:
        if self.budget <= 0:
            return None
        self.budget -= 1
        r = self.http.get(url, lane="web", scope=self.scope)
        self.snaps.put(r)
        return r

    def allowed(self, url: str) -> bool:
        p = urlsplit(url)
        origin = f"{p.scheme}://{p.netloc}"
        rp = self._robots(origin)
        if rp is None or rp == "unreachable":
            return None
        return rp.can_fetch(self.cfg.user_agent.split("/")[0], url) and rp.can_fetch("*", url)

    def fetch(self, url: str) -> tuple[Response | None, Doc | None, str]:
        """Returns (response, parsed doc, outcome) where outcome is ok|blocked|failed|budget."""
        try:
            ok = self.allowed(url)
            if ok is None:
                return None, None, "failed"
            if not ok:
                return None, None, "blocked"
            r = self._get(url)
        except BudgetExceeded:
            return None, None, "budget"
        if r is None:
            return None, None, "budget"
        if not r.ok:
            return r, None, "failed"
        if r.final_url != url and urlsplit(r.final_url).netloc != urlsplit(url).netloc:
            try:
                if not self.allowed(r.final_url):
                    return r, None, "blocked"
            except BudgetExceeded:
                pass
        ctype = r.content_type
        doc = parse(r.text()) if ("html" in ctype or not ctype) else None
        self.pages.append((r, doc))
        return r, doc, "ok"


# ---------------------------------------------------------------------------------------------
# per-company website logic

def _candidates(s: CompanyState) -> list[tuple[str, str]]:
    """(url, origin) in priority order: declared sources first, guesses last."""
    out: list[tuple[str, str]] = []
    reg = normalize_site(s.body().get("hjemmeside", ""))
    if reg:
        out.append((reg, "registry"))
    for f in s.nav_ads:
        hp = normalize_site(((f.data or {}).get("employer") or {}).get("homepage") or "")
        if hp:
            out.append((hp, "nav_employer_homepage"))
    email = (s.body().get("epostadresse") or "").strip().lower()
    if "@" in email:
        dom = email.split("@", 1)[1]
        if dom and dom not in FREE_MAIL and "." in dom:
            out.append((f"https://{dom}/", "registered_email_domain"))
    seen, res = set(), []
    for u, o in out:
        k = registrable(urlsplit(u).hostname or "")
        if k and k not in seen:
            seen.add(k)
            res.append((u, o))
    return res


def _guesses(s: CompanyState, known: set[str], limit: int = 3) -> list[tuple[str, str]]:
    """Domains spelled exactly like the legal name, .no first, then .com. Candidates only: a guess is
    published only if the site shows this org number, or the name plus the registered phone/email."""
    res: list[tuple[str, str]] = []
    labels = [d.rsplit(".", 1)[0] for d in domain_candidates(s.name())]
    for tld in ("no", "com"):
        for label in labels:
            d = f"{label}.{tld}"
            if registrable(d) in known:
                continue
            for host in (d, "www." + d):
                if _resolves(host):
                    res.append((f"https://{host}/", "name_guess"))
                    known.add(registrable(d))
                    break
            if len(res) >= limit:
                return res
    return res


def _norm_phone(raw: str) -> str | None:
    d = re.sub(r"\D", "", raw or "")
    if d.startswith("0047"):
        d = d[4:]
    elif d.startswith("47") and len(d) == 10:
        d = d[2:]
    return f"+47{d}" if len(d) == 8 and d[0] in "2345679" else None


def clean_title(t: str) -> str:
    t = re.sub("[\u00ad\u200b\u200c\u200d\ufeff]", "", t or "")
    return re.sub(r"\s+", " ", t).strip()


def _phones(body: dict) -> list[str]:
    out = []
    for k in ("telefon", "mobil"):
        d = re.sub(r"\D", "", body.get(k) or "")
        if d.startswith("47") and len(d) == 10:
            d = d[2:]
        if len(d) == 8:
            out.append(d)
    return out


def _phone_on(raw: str, phones: list[str]) -> re.Match | None:
    for p in phones:
        m = re.search(r"(?<!\d)" + r"[\s.\-]?".join(p) + r"(?!\d)", raw)
        if m:
            return m
    return None


def _domain_and_brand_match(legal: str, url: str, doc: Doc) -> bool:
    """For a site the company itself declared: the domain label and the site's own brand name are both
    made of words from the legal name (e.g. sagen.no / "Sagen" for SAGEN BYGG AS)."""
    from .names import LEGAL_SUFFIXES, fold, tokens
    name_toks = {t for t in tokens(legal) if t not in LEGAL_SUFFIXES}
    label = fold(registrable(urlsplit(url).hostname or "").split(".")[0].replace("-", " "))
    label_toks = set(label.split())
    squeezed = label.replace(" ", "")
    dom_ok = (label_toks and label_toks <= name_toks) or any(len(t) >= 4 and squeezed == t for t in name_toks) or \
        (len(squeezed) >= 5 and squeezed == "".join(t for t in tokens(legal) if t not in LEGAL_SUFFIXES))
    brand = doc.metas.get("og:site_name") or re.split(r"\s[|–\-·:]\s", doc.title or "")[-1]
    btoks = set(tokens(brand)) - LEGAL_SUFFIXES
    return bool(dom_ok and btoks and btoks <= name_toks and any(len(t) >= 4 for t in btoks))


ORG_TYPES = {"organization", "corporation", "localbusiness", "store", "restaurant", "professionalservice",
             "homeandconstructionbusiness", "autodealer", "medicalbusiness", "legalservice", "accountingservice",
             "financialservice", "realestateagent", "lodgingbusiness", "foodestablishment", "automotivebusiness",
             "healthandbeautybusiness", "sportsactivitylocation"}


def _prominent_text(pages) -> str:
    """Where a site names itself: title, site name, description, Organization JSON-LD, h1, footer, ©-lines."""
    parts: list[str] = []
    for _, d in pages:
        if d is None:
            continue
        parts += [d.title, d.metas.get("og:site_name", ""), d.metas.get("og:title", ""),
                  d.metas.get("description", ""), d.metas.get("og:description", "")]
        for obj in d.jsonld:
            if types_of(obj) & ORG_TYPES and isinstance(obj.get("name"), str):
                parts.append(obj["name"])
        for tag in ("h1", "footer"):
            for n in d.root.iter(tag):
                parts.append(n.text()[:3000])
        for m in re.finditer(r"(©|\(c\)|copyright)[^\n]{0,140}", d.text, re.I):
            parts.append(m.group(0))
    return " | ".join(p for p in parts if p)


HOSTNAME = re.compile(r"\b[\w\-]+(?:\.[\w\-]+)*\.(?:no|com|net|org|se|dk|fi|eu|io|app|biz|info|nu|as|co|uk|de|"
                      r"example|online|site|shop|store|tech)\b", re.I)


def _no_hostnames(text: str) -> str:
    return HOSTNAME.sub(" ", text or "")


def _domain_from_name(legal: str, url: str) -> bool:
    """The site's registrable domain label is the legal name's words joined (gangstadbygg.no for
    GANGSTAD BYGG AS), allowing Norwegian transliterations and hyphens."""
    label = registrable(urlsplit(url).hostname or "").split(".")[0].replace("-", "").lower()
    if len(label) < 4:
        return False
    return any(c.split(".")[0].replace("-", "") == label for c in domain_candidates(legal))


def _verify(s: CompanyState, crawler: SiteCrawler, url: str, origin: str) -> dict:
    """Fetch homepage (+ up to 2 contact/about pages) and decide the identity verdict."""
    res: dict = {"candidate": url, "origin": origin, "verdict": "failed", "pages": []}
    r, doc, outcome = crawler.fetch(url)
    if outcome == "failed" and url.startswith("https://"):
        alt = "http://" + url[len("https://"):]
        r2, doc2, outcome2 = crawler.fetch(alt)
        if outcome2 == "ok":
            r, doc, outcome = r2, doc2, outcome2
    if outcome != "ok" or doc is None:
        res["verdict"] = {"blocked": "blocked", "budget": "not_checked"}.get(outcome, "failed")
        res["detail"] = (f"HTTP {r.status}" if r is not None and r.status else (r.error if r is not None else outcome))
        return res
    res["home"] = r
    raw = r.text()
    if PARKED.search(doc.title or "") or (PARKED.search(doc.text[:3000]) and len(doc.text) < 4000):
        res["verdict"], res["detail"] = "rejected", "placeholder or parked page"
        return res
    pat = orgnr_pattern(s.orgnr)
    phones = _phones(s.body())
    email = (s.body().get("epostadresse") or "").strip().lower()
    checked: list[tuple[Response, Doc]] = [(r, doc)]
    hit = None

    def look(resp: Response, d: Doc):
        txt = resp.text()
        m = pat.search(txt)
        others = labelled_orgnrs(txt) - {s.orgnr}
        return txt, m, others

    raw, m, others = look(r, doc)
    hit = (r, m, raw) if m else None
    other_all = set(others)
    if not hit:
        home_host = registrable(urlsplit(r.final_url).hostname or "")
        links = []
        for a in doc.anchors:
            target = urljoin(r.final_url, a.href)
            if registrable(urlsplit(target).hostname or "") != home_host or target.split("#")[0] == r.final_url:
                continue
            path = urlsplit(target).path
            score = (2 if CONTACT_TEXT.match(a.text or "") else 0) + (1 if CONTACT_PATH.search(path) else 0)
            if score:
                links.append((-score, len(links), target.split("#")[0]))
        ordered = [t for _, _, t in sorted(links)]
        targets = list(dict.fromkeys(ordered))[:2]
        base = f"{urlsplit(r.final_url).scheme}://{urlsplit(r.final_url).netloc}"
        # Only for candidates that must prove themselves by org number (guesses, email domains).
        probes = ([] if origin in ("registry", "nav_employer_homepage") else
                  [base + p for p in PROBE_PATHS if base + p not in targets][: max(0, 3 - len(targets))])
        for target in targets + probes:
            if hit:
                break
            r2, d2, o2 = crawler.fetch(target)
            if o2 != "ok" or d2 is None:
                continue
            if r2 is not None and registrable(urlsplit(r2.final_url).hostname or "") != home_host:
                continue  # a probe that redirects off-site proves nothing
            checked.append((r2, d2))
            raw2, m2, others2 = look(r2, d2)
            other_all |= others2
            if m2:
                hit = (r2, m2, raw2)
    res["pages"] = checked
    all_text = " ".join(d.title + " " + d.metas.get("og:site_name", "") + " " + d.text for _, d in checked)
    raw_all = " ".join(x.text() for x, _ in checked)
    legal = s.name()
    # A hostname in the text ("gangstadbygg.no is parked") is not the company naming itself.
    name_any = page_names_company(legal, _no_hostnames(all_text))
    name_prominent = page_names_company(legal, _no_hostnames(_prominent_text(checked)))
    brand_dom = _domain_and_brand_match(legal, r.final_url, doc)
    dom_from_name = _domain_from_name(legal, r.final_url) or brand_dom
    phone_m = _phone_on(raw_all, phones)
    email_ok = bool(email) and email in raw_all.lower()
    contact = bool(phone_m or email_ok)
    directory = len(other_all) > 3
    # Only the register (or a NAV ad already tied to this org number) declares a website. An email domain
    # is NOT a declaration: it is often an employer, accountant, property manager or service provider.
    declared = origin in ("registry", "nav_employer_homepage")

    def contact_span() -> tuple[Response, str]:
        # Quote from one page only, so the span replays from that page's snapshot.
        for x, _ in checked:
            t = x.text()
            m = _phone_on(t, phones)
            if m:
                return x, span(t, m.start(), m.end())
            if email:
                i = t.lower().find(email)
                if i >= 0:
                    return x, span(t, i, i + len(email))
        return r, ""

    def name_proof() -> tuple[Response, str]:
        for x, _ in checked:
            sp = name_span(x.text(), legal)
            if sp and sp in x.text():
                return x, sp
        return r, name_span(r.text(), legal)

    if hit and not directory:
        resp, m, raw_hit = hit
        res.update(verdict="verified", method="org_number_on_site", confidence=0.98, proof=resp,
                   span=span(raw_hit, m.start(), m.end()))
    elif other_all and not hit:
        res.update(verdict="other_entity" if declared else "rejected",
                   detail="site shows a different organisation number: " + ", ".join(sorted(other_all)[:3]),
                   other_orgnrs=sorted(other_all))
    elif declared and (name_prominent or brand_dom):
        prf, sp = name_proof()
        res.update(verdict="verified", method=f"declared_by_{origin}+name_on_site", confidence=0.92, proof=prf,
                   span=sp)
    elif declared and name_any and contact:
        prf, sp = contact_span()
        res.update(verdict="verified", method=f"declared_by_{origin}+name_and_contact_on_site", confidence=0.9,
                   proof=prf, span=sp)
    elif origin == "registered_email_domain" and dom_from_name and (name_prominent or brand_dom or (name_any and contact)):
        # The company's own registered email uses a domain named after it: strong enough with the name shown.
        if contact:
            prf, sp = contact_span()
        else:
            prf, sp = name_proof()
        res.update(verdict="verified", method=f"{origin}+domain_matches_legal_name+name_on_site", confidence=0.9,
                   proof=prf, span=sp)
    elif origin == "name_guess" and name_any and contact:
        # A guessed domain needs the name AND the company's registered phone or email on the site
        # (namesakes are common), unless the org number itself is shown (handled above).
        if contact:
            prf, sp = contact_span()
        else:
            prf, sp = name_proof()
        res.update(verdict="verified", method="name_guess+name_and_registered_contact_on_site", confidence=0.9,
                   proof=prf, span=sp)
    elif declared:
        res.update(verdict="unconfirmed", detail="site reachable but does not show the org number, and the "
                                                 "company name is not in its title, headings or footer")
    else:
        why = {"registered_email_domain": "the registered email's domain is not named after this company and "
                                          "the site does not show its org number (likely an employer, "
                                          "accountant or manager)",
               "name_guess": "guessed domain does not prove this company"}.get(origin, "candidate not proven")
        res.update(verdict="rejected", detail=why)
    if res["verdict"] == "verified":
        res["shared"] = shared_site_reason(legal, (s.body().get("hjemmeside") or "") if origin == "registry" else "",
                                           r.final_url, doc, res["method"], name_prominent)
    return res


def _site_brands(doc: Doc) -> list[str]:
    """How the site names itself: Organization JSON-LD name, og:site_name, the last title segment."""
    out = [obj["name"] for obj in doc.jsonld if types_of(obj) & ORG_TYPES and isinstance(obj.get("name"), str)]
    out.append(doc.metas.get("og:site_name") or "")
    parts = re.split(r"\s[|–\-·:—]\s", doc.title or "")
    out.append(parts[-1] if parts else "")
    return [b.strip() for b in out if b and b.strip()]


def shared_site_reason(legal: str, registered_site: str, final_url: str, doc: Doc, method: str,
                       name_full: bool) -> str | None:
    """Why a verified website is a group or brand site rather than this legal entity's own (or None).
    The website itself is still published (the register or a NAV ad declares it), but the jobs, profiles,
    news, contact details and description on it are not attributed to this entity."""
    if method == "org_number_on_site":
        return None  # the site names this exact entity
    path = urlsplit(final_url).path
    if LOCALE_PATH.match(path):
        return f"the website is a country section ({path.split('/')[1]}) of a multinational site"
    if registered_site:
        rp = urlsplit(registered_site if "://" in registered_site else "https://" + registered_site).path.strip("/")
        if rp:
            return f"the register points to a section ({registered_site.strip()}) of a larger website"
    if _domain_from_name(legal, final_url):
        return None  # the domain is the full legal name spelled out
    legal_core = set(core_tokens(legal))
    for brand in _site_brands(doc):
        bc = set(core_tokens(brand))
        if bc and bc < legal_core:
            return (f"the site presents itself as \u201c{brand}\u201d, only part of the legal name {legal}, "
                    f"so it is likely a group or brand website")
    if method.startswith("declared_by_") and method.endswith("+name_on_site") and not name_full:
        return f"the site shows only a brand shared with {legal}, not the full company name"
    return None


_KEY_TABLES = ({"Æ": "AE", "Ø": "O", "Å": "A"}, {"Æ": "AE", "Ø": "OE", "Å": "AA"})


def _keys(text: str) -> set[str]:
    """Lower-case ASCII squeezes of a name or handle, in both Norwegian transliterations."""
    f = fold(text)
    return {re.sub(r"[^a-z0-9]", "", "".join(t.get(c, c) for c in f).lower()) for t in _KEY_TABLES}


def _social_handle(platform: str, path: str) -> tuple[str, bool]:
    """(handle, is_numeric_id) for a profile path that already matched SOCIAL."""
    parts = [x for x in path.split("/") if x]
    if not parts:
        return "", False
    if platform == "facebook":
        if parts[0].lower() == "profile.php":
            return "", True
        if parts[0].lower() == "pages":
            return (parts[1] if len(parts) > 1 else ""), False
        return parts[0], False
    if platform == "linkedin":
        slug = parts[1] if len(parts) > 1 else ""
        return slug, slug.isdigit()
    if platform == "youtube":
        if parts[0] == "channel":
            return "", True
        return (parts[1] if parts[0] in ("c", "user") and len(parts) > 1 else parts[0].lstrip("@")), False
    return parts[0].lstrip("@"), False


def _profile_names_company(handle: str, needles: set[str]) -> bool:
    return any(n in k for k in _keys(handle) for n in needles)


def _socials(pages: list[tuple[Response, Doc]], home: str, names: list[str] | None = None,
             label: str = "") -> dict[str, list[dict]]:
    """Company profiles linked from its verified website. A link counts only if its handle carries the
    company's name, brand or domain label; numeric-id profiles only when declared in the site's own
    structured data or linked from its homepage, and only as the single link for that platform.
    Website-builder defaults (wix, squarespace...), legal pages and agency/designer credits are dropped."""
    needles: set[str] = set()
    for n in names or []:
        core = core_tokens(n)
        for k in _keys(" ".join(core)):
            if len(k) >= 4:
                needles.add(k)
        if core:  # the distinctive first word ("Mucha", "Myhre"), never a later industry word ("dental")
            needles |= {k for k in _keys(core[0]) if len(k) >= 5}
    needles |= {k for k in _keys(label.replace("-", " ")) if len(k) >= 3}
    found: dict[str, dict[str, dict]] = {}
    for r, d in pages:
        if d is None:
            continue
        hrefs = [(a.href, r, False) for a in d.anchors]
        for obj in d.jsonld:
            same = obj.get("sameAs")
            for u in (same if isinstance(same, list) else [same] if same else []):
                if isinstance(u, str):
                    hrefs.append((u, r, True))
        for href, resp, declared in hrefs:
            u = urljoin(resp.final_url, href.strip())
            p = urlsplit(u)
            host = (p.hostname or "").lower()
            for platform, (hp, pp) in SOCIAL.items():
                if re.search(hp, host) and re.match(pp, p.path + (("?" + p.query) if p.query and "id=" in p.query else "")):
                    handle, is_id = _social_handle(platform, p.path)
                    if is_id:
                        if not (declared or resp.final_url == home):
                            continue
                    elif not handle or _keys(handle) & SOCIAL_NOT_A_COMPANY or \
                            (needles and not _profile_names_company(handle, needles)):
                        continue
                    if platform == "linkedin" or host.startswith("m.") or re.match(r"^[a-z]{2}\.", host):
                        host = "www." + re.sub(r"^(www|m|[a-z]{2})\.", "", host)
                    path = p.path.rstrip("/")
                    if platform == "linkedin":
                        path = "/" + "/".join([x for x in path.split("/") if x][:2])
                    clean = urlunsplit(("https", host, path, p.query if "id=" in p.query else "", ""))
                    key = f"id:{p.query or p.path}".lower() if is_id else min(_keys(handle))
                    found.setdefault(platform, {}).setdefault(key, {"url": clean, "page": resp, "is_id": is_id})
    out: dict[str, list[dict]] = {}
    for platform, items in found.items():
        vals = list(items.values())
        if len(vals) > 3:
            continue  # many handles for one platform: likely a directory or agency footer
        if any(v["is_id"] for v in vals) and len(vals) > 1:
            vals = [v for v in vals if not v["is_id"]]  # an unnamed id next to named profiles: unprovable
        out[platform] = [{"url": v["url"], "page": v["page"]} for v in vals]
    return out


def _org_jsonld(pages) -> tuple[dict | None, Response | None]:
    for r, d in pages:
        if d is None:
            continue
        for obj in d.jsonld:
            if types_of(obj) & ORG_TYPES:
                return obj, r
    return None, None


def _addresses(obj: dict) -> list[dict]:
    out = []
    addrs = obj.get("address")
    for a in (addrs if isinstance(addrs, list) else [addrs] if addrs else []):
        if isinstance(a, dict):
            x = {k: a.get(k) for k in ("streetAddress", "postalCode", "addressLocality", "addressRegion",
                                         "addressCountry") if isinstance(a.get(k), str) and a.get(k).strip()}
            if x:
                out.append(x)
        elif isinstance(a, str) and a.strip():
            out.append({"text": a.strip()})
    return out


def _jobpostings(d: Doc, r: Response, names_ok) -> list[dict]:
    out = []
    for obj in d.jsonld:
        if "jobposting" not in types_of(obj):
            continue
        org = obj.get("hiringOrganization")
        oname = org.get("name") if isinstance(org, dict) else (org if isinstance(org, str) else None)
        if oname and not names_ok(oname):
            continue  # posting for another employer (agency, parent): never attributed here
        loc = obj.get("jobLocation")
        locs = []
        for l in (loc if isinstance(loc, list) else [loc] if loc else []):
            a = (l or {}).get("address") if isinstance(l, dict) else None
            if isinstance(a, dict):
                locs.append(", ".join(str(a.get(k)) for k in ("addressLocality", "addressRegion") if a.get(k)))
        title = obj.get("title") or obj.get("name")
        if not isinstance(title, str) or not title.strip():
            continue
        out.append({"title": clean_title(title)[:200], "date_posted": _date(str(obj.get("datePosted") or "")),
                    "valid_through": _date(str(obj.get("validThrough") or "")),
                    "location": "; ".join(x for x in locs if x) or None,
                    "url": urljoin(r.final_url, obj.get("url")) if isinstance(obj.get("url"), str) else r.final_url,
                    "hiring_organization": oname, "method": "json-ld JobPosting"})
    return out


def careers_link_ok(url: str, own_host: str) -> bool:
    """A careers page worth reading: on the company's own site, or on an ATS that hosts only its
    employer's postings. Public job boards are excluded (their pages list other employers' jobs)."""
    host = (urlsplit(url).hostname or "").lower()
    if registrable(host) == own_host:
        return True
    return bool(ATS_HOSTS.search(host)) and not JOB_BOARDS.search(host)


def _job_links(d: Doc, r: Response, own_host: str) -> list[dict]:
    """Postings linked from a careers page. ATS links are postings by their URL shape; links on the
    company's own site must also read like a job title. Navigation ("Hopp til innhold"), links back to the
    page itself and texts about working there ("Se våre ledige stillinger") are never postings. On an
    external recruitment page only postings on that same recruitment site count."""
    out, seen = [], set()
    here = r.final_url.split("#")[0].rstrip("/")
    page_name = re.sub(r"^www\.", "", (urlsplit(r.final_url).hostname or "").lower())
    external = registrable(page_name) != own_host
    for a in d.anchors:
        href = (a.href or "").strip()
        if not href or href.startswith("#") or href.lower().startswith(("javascript:", "mailto:", "tel:")):
            continue
        u = urljoin(r.final_url, href).split("#")[0]
        text = re.sub(r"\s+", " ", a.text or a.title).strip()
        if u.rstrip("/") == here or len(text) < 4 or len(text) > 160 or u in seen:
            continue
        if GENERIC_LINK.match(text):
            blk = a.node.block()
            head = next((h.text() for t in ("h1", "h2", "h3", "h4", "strong") for h in blk.iter(t) if len(h.text()) >= 4), "")
            text = head or ""
            if len(text) < 4:
                continue
        text = TRAILING_CTA.sub("", text).strip()
        if len(text) < 4 or CAREER_TALK.search(text):
            continue
        host = registrable(urlsplit(u).hostname or "")
        link_name = re.sub(r"^www\.", "", (urlsplit(u).hostname or "").lower())
        if external and (link_name != page_name or JOB_BOARDS.search(link_name)):
            continue  # a recruitment page linking elsewhere (or a job board): not this employer's posting
        if JOB_LINK.search(u):
            seen.add(u)
            out.append({"title": text, "url": u, "method": "ATS job link on careers page"})
        elif host == own_host and SITE_JOB_PATH.search(urlsplit(u).path) and not CAREER_WORDS.fullmatch(text) \
                and len(text.split()) >= 2 and JOB_TITLE_HINT.search(text):
            seen.add(u)
            out.append({"title": text, "url": u, "method": "job detail link on own careers page"})
    return out[:40]


def _news_from_doc(d: Doc, r: Response, window: int) -> list[dict]:
    items, seen = [], set()
    for obj in d.jsonld:
        if types_of(obj) & {"newsarticle", "article", "blogposting", "pressrelease"}:
            t = obj.get("headline") or obj.get("name")
            dt = _date(str(obj.get("datePublished") or ""))
            u = obj.get("url") if isinstance(obj.get("url"), str) else r.final_url
            if isinstance(t, str) and dt and _in_window(dt, window):
                u = urljoin(r.final_url, u)
                if u not in seen:
                    seen.add(u)
                    items.append({"date": dt, "title": clean_title(t)[:200], "url": u, "method": "json-ld Article"})
    for a in d.anchors:
        u = urljoin(r.final_url, a.href).split("#")[0]
        txt = a.text or a.title
        if u in seen or u == r.final_url or len(txt) < 12 or len(txt) > 200:
            continue
        m = re.search(r"/(20\d\d)/(\d{1,2})/(\d{1,2})/", urlsplit(u).path)
        dt = None
        if m:
            try:
                dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).date().isoformat()
            except ValueError:
                dt = None
        if not dt:
            blk = a.node.block()
            btxt = blk.text() if blk is not None else ""
            if len(btxt) <= 400:
                found = {x for x in (_date(y) for y in re.findall(r"\d{1,2}\.\s?\d{1,2}\.\s?\d{4}|\d{1,2}\.?\s+[A-Za-zæøå]{3,9}\.?\s+\d{4}|\d{4}-\d{2}-\d{2}", btxt)) if x}
                if len(found) == 1:
                    dt = found.pop()
        if dt and _in_window(dt, window):
            seen.add(u)
            items.append({"date": dt, "title": re.sub(r"\s+", " ", txt).strip()[:200], "url": u,
                          "method": "dated link in news list"})
    for dt_attr, dt_text, node in d.times:
        dt = _date(dt_attr) or _date(dt_text)
        if not dt or not _in_window(dt, window):
            continue
        blk = node.block()
        best = None
        for a in blk.iter("a"):
            txt = a.text()
            if len(txt) >= 12 and (best is None or len(txt) > len(best[1])):
                best = (a.attrs.get("href", ""), txt)
        if best:
            u = urljoin(r.final_url, best[0]).split("#")[0]
            if u not in seen and u != r.final_url:
                seen.add(u)
                items.append({"date": dt, "title": best[1][:200], "url": u, "method": "<time> + link in same block"})
    return items


def _news_from_feed(r: Response, window: int) -> list[dict]:
    out = []
    try:
        root = ET.fromstring(r.body[:2_000_000])
    except ET.ParseError:
        return out
    for it in list(root.iter("item")) + list(root.iter("{http://www.w3.org/2005/Atom}entry")):
        def g(tag):
            el = it.find(tag)
            return (el.text or "").strip() if el is not None and el.text else ""
        title = g("title") or g("{http://www.w3.org/2005/Atom}title")
        link = g("link")
        if not link:
            el = it.find("{http://www.w3.org/2005/Atom}link")
            link = el.get("href", "") if el is not None else ""
        dt = _date(g("pubDate") or g("{http://www.w3.org/2005/Atom}published") or
                   g("{http://www.w3.org/2005/Atom}updated") or g("{http://purl.org/dc/elements/1.1/}date"))
        title = clean_title(title)
        if title and dt and _in_window(dt, window):
            out.append({"date": dt, "title": title[:200], "url": urljoin(r.final_url, link) if link else r.final_url,
                        "method": "RSS/Atom feed"})
    return out


def crawl_company(http: Http, cfg: Config, snaps: SnapshotStore, robots_cache: dict, s: CompanyState) -> None:
    w: dict = {"candidates": [], "verified": None, "attempts": []}
    s.web = w
    if not s.name():
        s.status["website"] = SourceStatus("not_applicable", "no registered name")
        return
    crawler = SiteCrawler(http, cfg, snaps, robots_cache, s.scope)
    cands = _candidates(s)
    known = {registrable(urlsplit(u).hostname or "") for u, _ in cands}
    verified = None

    def attempt(batch):
        nonlocal verified
        for url, origin in batch:
            if verified or not http.can_start():
                return
            v = _verify(s, crawler, url, origin)
            w["attempts"].append(v)
            if v["verdict"] == "verified":
                verified = v

    attempt(cands)
    if not verified and cfg.enable_domain_guess and http.can_start():
        guesses = _guesses(s, known)
        cands += guesses
        attempt(guesses)
    w["candidates"] = [{"url": u, "origin": o} for u, o in cands]
    if not cands:
        s.status["website"] = SourceStatus("not_found", "no website declared in the register or on NAV ads, "
                                                        "no company email domain, and no .no or .com domain spelled "
                                                        "like the legal name resolved")
        return
    if not verified:
        declared = [a for a in w["attempts"] if a["origin"] in ("registry", "nav_employer_homepage")]
        verdicts = [a["verdict"] for a in declared]
        if not declared:
            tried = "; ".join(f"{a['candidate']} ({a['origin']}): {a.get('detail') or a['verdict']}"
                              for a in w["attempts"]) or "none"
            s.status["website"] = SourceStatus("not_found", "no website declared in the register or on a NAV ad "
                                                            f"for this org number; other candidates did not prove "
                                                            f"this company: {tried}")
            return
        if "other_entity" in verdicts or "unconfirmed" in verdicts:
            s.status["website"] = SourceStatus("ambiguous", "; ".join(a.get("detail", "") for a in declared
                                                                      if a["verdict"] in ("other_entity", "unconfirmed")))
        elif "blocked" in verdicts:
            s.status["website"] = SourceStatus("blocked", "robots.txt disallows our crawler")
        elif "failed" in verdicts:
            s.status["website"] = SourceStatus("failed", "; ".join(f"{a['candidate']}: {a.get('detail')}" for a in declared))
        elif all(v == "not_checked" for v in verdicts) or not verdicts:
            s.status["website"] = SourceStatus("not_checked", "run budget reached before the website was checked")
        else:
            s.status["website"] = SourceStatus("not_found", "; ".join(f"{a['candidate']}: {a.get('detail')}" for a in declared))
        return
    w["verified"] = verified
    s.status["website"] = SourceStatus("checked", verified["method"])
    pages = list(verified["pages"])
    home: Response = verified["home"]
    home_doc = pages[0][1]
    own = registrable(urlsplit(home.final_url).hostname or "")
    if verified.get("shared"):
        # A group or brand site: the address is the company's (declared), the content is not its own.
        w["shared_site"] = verified["shared"]
        w["careers_status"] = f"not read: {verified['shared']}"
        return
    brand_names = {s.name()}
    org_obj, org_resp = _org_jsonld(pages)
    w["org_jsonld"] = (org_obj, org_resp) if org_obj else None
    if org_obj and isinstance(org_obj.get("name"), str):
        brand_names.add(org_obj["name"])
    if home_doc.metas.get("og:site_name"):
        brand_names.add(home_doc.metas["og:site_name"])

    def names_ok(n: str) -> bool:
        return any(page_names_company(b, n) or page_names_company(n, b) for b in brand_names if b)

    # ---- careers ------------------------------------------------------------------------
    jobs: list[dict] = []
    careers = None
    for r0, d0 in pages:
        jobs += [dict(j, page=r0) for j in _jobpostings(d0, r0, names_ok)]
    cand_links = []
    for r0, d0 in pages:
        for a in d0.anchors:
            u = urljoin(r0.final_url, a.href).split("#")[0]
            host = urlsplit(u).hostname or ""
            if not u.startswith("http"):
                continue
            if CAREER_WORDS.search(a.text + " " + urlsplit(u).path) and careers_link_ok(u, own):
                cand_links.append(u)
    w["careers_status"] = "no careers link found on homepage/contact pages"
    for u in list(dict.fromkeys(cand_links))[:2]:
        rc, dc, oc = crawler.fetch(u)
        if oc == "blocked":
            w["careers_status"] = "careers page blocked by robots.txt"
            continue
        if oc != "ok" or dc is None:
            w["careers_status"] = f"careers page fetch {oc}"
            continue
        careers = rc
        w["careers_status"] = "checked"
        jobs += [dict(j, page=rc) for j in _jobpostings(dc, rc, names_ok)]
        jobs += [dict(j, page=rc) for j in _job_links(dc, rc, own)]
        m = NO_VACANCY.search(dc.text)
        if m:
            w["no_vacancy"] = (rc, m.group(0))
        break
    dedup, seen = [], set()
    for j in jobs:
        k = (j.get("url") or "", j["title"].lower())
        if k not in seen:
            seen.add(k)
            dedup.append(j)
    w["jobs"] = dedup[:40]
    w["careers"] = careers

    # ---- news / dated activity -------------------------------------------------------------
    news: list[dict] = []
    feed_url = None
    for l in home_doc.links:
        if "alternate" in (l.get("rel") or "").lower() and re.search(r"rss|atom", l.get("type", ""), re.I):
            feed_url = urljoin(home.final_url, l.get("href", ""))
            if "comments" not in feed_url:
                break
    if feed_url and registrable(urlsplit(feed_url).hostname or "") == own:
        rf, _, of = crawler.fetch(feed_url)
        if of == "ok" and rf is not None:
            news += [dict(n, page=rf) for n in _news_from_feed(rf, cfg.activity_window_days)]
    for r0, d0 in pages:
        news += [dict(n, page=r0) for n in _news_from_doc(d0, r0, cfg.activity_window_days)]
    if len(news) < 3:
        nl = []
        for a in home_doc.anchors:
            u = urljoin(home.final_url, a.href).split("#")[0]
            if registrable(urlsplit(u).hostname or "") == own and NEWS_WORDS.search(a.text + " " + urlsplit(u).path):
                nl.append(u)
        for u in list(dict.fromkeys(nl))[:1]:
            rn, dn, on = crawler.fetch(u)
            if on == "ok" and dn is not None:
                news += [dict(n, page=rn) for n in _news_from_doc(dn, rn, cfg.activity_window_days)]
    dedup, seen = [], set()
    for n in sorted(news, key=lambda x: x["date"], reverse=True):
        if n["url"] not in seen:
            seen.add(n["url"])
            dedup.append(n)
    w["news"] = dedup[:12]

    # ---- profiles, contact, description ---------------------------------------------------
    all_pages = [(r0, d0) for r0, d0 in crawler.pages if d0 is not None
                 and registrable(urlsplit(r0.final_url).hostname or "") == own]
    w["socials"] = _socials(all_pages, home.final_url, sorted(brand_names), own.split(".")[0])
    emails, phones = {}, {}
    for r0, d0 in all_pages:
        for a in d0.anchors:
            h = a.href.strip()
            if h.lower().startswith("mailto:"):
                e = h[7:].split("?")[0].strip().lower()
                if re.fullmatch(r"[^@\s]+@[^@\s]+\.[a-z]{2,}", e):
                    emails.setdefault(e, r0)
            elif h.lower().startswith("tel:"):
                p = _norm_phone(h[4:])
                if p:
                    phones.setdefault(p, (r0, h))
    w["emails"] = list(emails.items())[:5]
    w["phones"] = list(phones.items())[:5]
    desc = home_doc.metas.get("description") or home_doc.metas.get("og:description") or ""
    w["description"] = re.sub(r"\s+", " ", desc).strip()[:400] or None
    brand = None
    if org_obj and isinstance(org_obj.get("name"), str):
        brand = org_obj["name"].strip()
    elif home_doc.metas.get("og:site_name"):
        brand = home_doc.metas["og:site_name"].strip()
    w["brand"] = brand
    w["addresses"] = _addresses(org_obj) if org_obj else []


def crawl_websites(http: Http, cfg: Config, snaps: SnapshotStore, states: list[CompanyState]) -> dict:
    live = [s for s in states if s.kind == "enhet" and s.identity.state == "checked"]
    stats = {"companies": len(live), "verified": 0, "ambiguous": 0, "blocked": 0, "failed": 0, "not_found": 0,
             "not_checked": 0}
    if not cfg.enable_web:
        for s in live:
            s.status["website"] = SourceStatus("not_checked", "website stage disabled")
        return stats
    robots_cache: dict = {}
    # Companies with a declared website first: they are the most likely to verify.
    order = sorted(live, key=lambda s: 0 if s.body().get("hjemmeside") or s.nav_ads else 1)

    def run(s: CompanyState) -> None:
        if not http.can_start(reserve_requests=2):
            s.status["website"] = SourceStatus("not_checked", "run budget reached before the website was checked")
            return
        try:
            crawl_company(http, cfg, snaps, robots_cache, s)
        except BudgetExceeded as e:
            s.status.setdefault("website", SourceStatus("not_checked", str(e)))
        except Exception as e:  # noqa: BLE001 - one odd site must never kill the run
            s.error("website", f"{type(e).__name__}: {e}")
            s.status["website"] = SourceStatus("failed", f"internal error while reading site: {type(e).__name__}")

    with ThreadPoolExecutor(max_workers=cfg.web_workers) as ex:
        list(ex.map(run, order))
    for s in live:
        st = s.status.get("website", SourceStatus("not_checked")).state
        key = {"checked": "verified"}.get(st, st)
        stats[key] = stats.get(key, 0) + 1
        if (s.web or {}).get("shared_site"):
            stats["verified_shared_group_site"] = stats.get("verified_shared_group_site", 0) + 1
    return stats
