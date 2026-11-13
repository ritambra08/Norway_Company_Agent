#!/usr/bin/env python3
"""List every verified official website (plus the jobs and social profiles that hang off it) from a
Signalpost run, so each one can be checked by hand for a wrong-company match.

Run from the project folder:  python3 scripts/audit_websites.py
Reads scale/envelopes.jsonl (or the path given as the first argument) and writes
~/Downloads/signalpost-website-audit.tsv. Standard library only; reads nothing from the network."""
import json
import re
import sys
from pathlib import Path

src = Path(sys.argv[1] if len(sys.argv) > 1 else "scale/envelopes.jsonl")
out = Path.home() / "Downloads" / "signalpost-website-audit.tsv"


def clean(text, n=220):
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = re.sub(r"\s+", " ", text).replace("\t", " ").strip()
    return text[:n]


rows = []
for line in src.read_text(encoding="utf-8").split("\n"):
    if not line.strip():
        continue
    e = json.loads(line)
    claims = {c["field"]: c for c in e.get("claims", [])}
    w = claims.get("official_website") or {}
    nav_jobs = [j for j in ((claims.get("job_postings") or {}).get("value") or []) if j.get("source") == "NAV job vacancy feed"]
    if w.get("availability") != "available" and not nav_jobs:
        continue
    v = w.get("value") or {}
    li = e.get("legal_identity") or {}
    ev = {x["id"]: x for x in e.get("evidence", [])}
    proof = " | ".join(clean(ev[i].get("claim_span")) for i in w.get("evidence_ids", [])
                       if i in ev and "brreg" not in (ev[i].get("source_url") or ""))
    contact = (claims.get("registered_contact") or {}).get("value") or {}
    addr = (claims.get("business_address") or {}).get("value") or {}
    ind = (claims.get("industry") or {}).get("value") or []
    jobs = (claims.get("job_postings") or {}).get("value") or []
    socials = (claims.get("social_profiles") or {}).get("value") or []
    news = (claims.get("website_news") or {}).get("value") or []
    careers = (claims.get("careers_page") or {}).get("value") or ""
    wcontact = (claims.get("website_contact") or {}).get("value") or {}
    rows.append([
        e.get("organisation_number", ""), li.get("name", ""), li.get("organisation_form", ""),
        addr.get("city", "") if isinstance(addr, dict) else "",
        (ind[0].get("description", "") if ind and isinstance(ind[0], dict) else ""),
        v.get("url", ""), v.get("final_url", ""), v.get("verification", ""), v.get("candidate_from", ""),
        "yes" if v.get("shared_with_group") else "",
        (claims.get("registered_website") or {}).get("value") or "",
        contact.get("email", "") if isinstance(contact, dict) else "",
        proof,
        str(len(jobs)), " ; ".join(clean(j.get("title"), 60) + " [" + (j.get("source") or "") + "]" for j in jobs[:4]),
        " ; ".join(s.get("url", "") for s in socials[:6]),
        str(len(news)), " ; ".join(f"{n.get('date')} {clean(n.get('title'), 60)} <{n.get('url')}>" for n in news[:3]),
        careers if isinstance(careers, str) else "",
        " ; ".join((wcontact.get("emails") or [])[:3]) if isinstance(wcontact, dict) else "",
    ])

head = ["org", "legal_name", "form", "city", "industry", "url", "final_url", "verification", "candidate_from",
        "shared_group_site", "registered_website", "registered_email", "proof_on_site", "jobs", "job_titles", "social_profiles",
        "news", "news_items", "careers_page", "website_emails"]
with out.open("w", encoding="utf-8") as fh:
    fh.write("\t".join(head) + "\n")
    for r in sorted(rows, key=lambda r: (r[9], r[7], r[1])):
        fh.write("\t".join(str(x).replace("\t", " ").replace("\n", " ") for x in r) + "\n")
print(f"Wrote {len(rows)} companies (verified website or NAV job ads) to {out}")
