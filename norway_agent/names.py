"""Company-name normalisation shared by job matching, website checks and domain candidates."""

from __future__ import annotations

import re
import unicodedata

LEGAL_SUFFIXES = {"AS", "ASA", "SA", "DA", "ANS", "BA", "NUF", "KS", "IKS", "SF", "ENK", "HF", "KF", "BBL",
                  "BRL", "SAM", "STI", "FLI", "ESEK", "PK", "SPA", "VPFO", "GFS", "PRE", "SE", "AB", "APS",
                  "LTD", "LIMITED", "GMBH", "INC", "LLC", "OY", "BV", "NV", "PLC", "UK", "AVD", "AVDELING",
                  "FILIAL", "NORGE", "NORWAY", "NORDIC"}
# Words too generic to identify a company on their own.
GENERIC = {"OG", "AND", "THE", "I", "PA", "PÅ", "AV", "FOR", "TIL", "MED", "HOLDING", "INVEST", "INVESTERING",
           "EIENDOM", "SERVICE", "SERVICES", "CONSULT", "CONSULTING", "GRUPPEN", "GROUP", "CO", "COMPANY",
           "BYGG", "DRIFT", "TRANSPORT", "ENTREPRENOR", "ENTREPRENØR", "REGNSKAP", "HANDEL", "SALG",
           "PROSJEKT", "MASKIN", "ELEKTRO", "RØR", "MALER", "TAKST", "UTLEIE", "NORSK", "NORSKE", "NY"}


def fold(s: str) -> str:
    """Upper-case, strip accents except the Norwegian letters, normalise '&' and punctuation."""
    s = unicodedata.normalize("NFKC", s or "").upper().replace("&", " OG ")
    s = "".join(ch for ch in unicodedata.normalize("NFD", s)
                if not (unicodedata.category(ch) == "Mn" and ch not in "̊"))  # keep Å ring
    s = unicodedata.normalize("NFC", s)
    s = re.sub(r"[^0-9A-ZÆØÅ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def tokens(name: str) -> list[str]:
    return [t for t in fold(name).split() if t]


def match_key(name: str) -> str:
    """Key for exact-name candidate matching: folded, legal-form words removed."""
    toks = [t for t in tokens(name) if t not in LEGAL_SUFFIXES]
    return " ".join(toks)


def core_tokens(name: str) -> list[str]:
    """Distinctive tokens that must all appear for a page to 'name' the company."""
    toks = [t for t in tokens(name) if t not in LEGAL_SUFFIXES]
    core = [t for t in toks if t not in GENERIC and (len(t) >= 3 or t.isdigit())]
    return core or [t for t in toks if len(t) >= 3]


def required_tokens(name: str) -> list[str]:
    """Words a page must show to 'name' the company. One distinctive token is weak evidence
    ("BERGEN BYGG" vs "Bergen kommune"), so then every non-legal-form word is required."""
    core = core_tokens(name)
    if len(core) < 2:
        core = [t for t in tokens(name) if t not in LEGAL_SUFFIXES] or core
    return core


def page_names_company(name: str, page_text: str) -> bool:
    core = required_tokens(name)
    if not core:
        return False
    hay = " " + fold(page_text) + " "
    squeezed = hay.replace(" ", "")
    if all(f" {t} " in hay for t in core):
        return True
    # Brand written as one word ("Jolima" for "JO LIMA AS")
    joined = "".join(core)
    return len(joined) >= 6 and joined in squeezed


_TRANSLIT = [{"Æ": "AE", "Ø": "O", "Å": "A"}, {"Æ": "AE", "Ø": "OE", "Å": "AA"}]


def domain_candidates(name: str) -> list[str]:
    """Plausible .no domains for a legal name. Only candidates: nothing is published unless the
    fetched site proves the exact organisation number (see web.py)."""
    toks = [t for t in tokens(name) if t not in LEGAL_SUFFIXES]
    if not toks or len(toks) > 4:
        return []
    out: list[str] = []
    for table in _TRANSLIT:
        tt = ["".join(table.get(c, c) for c in t).lower() for t in toks]
        tt = [re.sub(r"[^a-z0-9]", "", t) for t in tt]
        tt = [t for t in tt if t]
        joined = "".join(tt)
        if len(joined) < 4 or len(joined) > 40 or joined.isdigit():
            continue
        out.append(f"{joined}.no")
        if len(tt) > 1:
            out.append(f"{'-'.join(tt)}.no")
    seen, res = set(), []
    for d in out:
        if d not in seen:
            seen.add(d)
            res.append(d)
    return res[:3]
