"""Profile cards (profiles.jsonl) and a single-file HTML viewer (index.html).

The viewer works offline on desktop and mobile: search, filter, sort, compare up to three
companies, and open the evidence behind every fact (source link, retrieval time, hash, span).
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path

from .summary import money

KEY_FIELDS = ["legal_name", "industry", "business_address", "annual_accounts_latest", "leadership",
              "registered_workplaces", "official_website", "social_profiles", "job_postings", "public_activity",
              "registered_employees", "business_description"]


def _strip(s: str) -> str:
    s = re.sub(r"<script.*?</script>|<style.*?</style>", " ", s or "", flags=re.S | re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", html.unescape(s)).strip()[:400]


def _fmt(field: str, v) -> str:
    if v is None:
        return ""
    if isinstance(v, dict) and "amount" in v:
        return money(v["amount"], v.get("currency", "NOK")) + (f" ({v['period_end'][:4]})" if v.get("period_end") else "")
    if field in ("business_address", "postal_address") and isinstance(v, dict):
        return ", ".join(x for x in [" ".join(v.get("lines") or []), v.get("postal_code", ""), v.get("city", "")] if x)
    if field == "industry" and isinstance(v, list):
        return "; ".join(f"{i.get('description')} ({i.get('code')})" for i in v)
    if field == "organisation_form" and isinstance(v, dict):
        return f"{v.get('description')} ({v.get('code')})"
    if field == "registered_employees" and isinstance(v, dict):
        return str(v.get("count", "fewer than 5"))
    if field == "business_description" and isinstance(v, dict):
        return v.get("activity") or v.get("statutory_purpose") or ""
    if field == "official_website" and isinstance(v, dict):
        return v.get("url", "")
    if field in ("vat_registered", "business_register") and isinstance(v, dict):
        return ("Yes" if v.get("registered") else "No") + (f", since {v['since']}" if v.get("since") else "")
    if field == "share_capital" and isinstance(v, dict):
        return money(v.get("amount"), v.get("currency", "NOK"))
    if field == "revenue_change" and isinstance(v, dict):
        return (f"{v['change_pct']:+.1f}% ({v['from_period_end'][:4]}→{v['to_period_end'][:4]})"
                if v.get("change_pct") is not None else "")
    if field == "registered_contact" and isinstance(v, dict):
        return " · ".join(str(x) for x in v.values())
    if field in ("chief_executive", "board_chair", "auditor", "accountant"):
        items = v if isinstance(v, list) else [v]
        return "; ".join(str(i.get("name")) for i in items if isinstance(i, dict))
    if field == "hiring_signal" and isinstance(v, dict):
        return (f"Yes – {v.get('open_postings')} open posting(s)" if v.get("hiring")
                else "No – the careers page says there are no open positions")
    if isinstance(v, bool):
        return "Yes" if v else "No"
    if isinstance(v, (str, int, float)):
        return str(v)
    return ""


def card(env: dict) -> dict:
    claims = {c["field"]: c for c in env.get("claims") or []}
    ev = {e["id"]: e for e in env.get("evidence") or []}
    srcs = env.get("sources") or {}

    table: list[dict] = []
    index: dict[str, int] = {}

    def evs(c):
        out = []
        for i in c.get("evidence_ids") or []:
            e = ev.get(i)
            if not e:
                continue
            if i not in index:
                src = srcs.get(e.get("source_ref") or "", {})
                index[i] = len(table)
                table.append({"s": src.get("source_name") or e.get("source_class"), "u": e.get("source_url"),
                              "t": e.get("retrieved_at"), "h": (e.get("content_sha256") or "")[:12],
                              "x": _strip(e.get("claim_span", ""))[:160],
                              "l": (e.get("locator") or {}).get("path") or e.get("extraction_method"),
                              "p": 1 if e.get("from_previous_run") else 0})
            out.append(index[i])
            if len(out) >= 4:
                break
        return out

    facts = {}
    for f, c in claims.items():
        text = _fmt(f, c.get("value")) if c["availability"] == "available" else ""
        x = {"label": c["label"], "state": c["availability"], "text": text}
        if c["availability"] == "available":
            if not text:
                x["value"] = c.get("value")
            x["ev"] = evs(c)
            if c.get("note"):
                x["note"] = c["note"]
            if c.get("stale"):
                x["stale"] = True
        else:
            x["reason"] = c.get("reason")
        facts[f] = x
    def val(f):
        c = claims.get(f)
        return c["value"] if c and c["availability"] == "available" else None
    latest = val("annual_accounts_latest") or {}
    figs = latest.get("figures") or {}
    addr = val("business_address") or {}
    emp = val("registered_employees") or {}
    jobs = val("job_postings")
    present = sum(1 for f in KEY_FIELDS if val(f) not in (None, [], {}))
    return {
        "org": env["organisation_number"], "status": env["status"],
        "name": val("legal_name") or (env.get("legal_identity") or {}).get("name") or env["organisation_number"],
        "brand": val("public_brand_name"), "form": (val("organisation_form") or {}).get("code"),
        "city": (addr.get("city") or addr.get("municipality") or "").title(),
        "industry": ((val("industry") or [{}])[0]).get("description"),
        "registry_status": val("registry_status"),
        "revenue": figs.get("revenue"), "net_result": figs.get("net_result"), "currency": latest.get("currency"),
        "year": (latest.get("period_end") or "")[:4] or None, "employees": emp.get("count"),
        "website": (val("official_website") or {}).get("url"),
        "jobs": len(jobs) if isinstance(jobs, list) else None,
        "completeness": round(present / len(KEY_FIELDS), 2),
        "changes": env.get("changes") or [], "summary": (env.get("summary") or {}).get("text", ""),
        "sections": env.get("availability") or {}, "facts": facts,
        "run": (env.get("run") or {}).get("run_id"), "errors": env.get("errors") or [], "E": table,
    }


def write_cards(envs: list[dict], path: Path) -> list[dict]:
    cards = [card(e) for e in envs]
    with Path(path).open("w", encoding="utf-8") as fh:
        for c in cards:
            fh.write(json.dumps(c, ensure_ascii=False) + "\n")
    return cards


def write_viewer(cards: list[dict], report: dict, path: Path) -> None:
    data = json.dumps({"cards": cards, "report": {k: report.get(k) for k in
                                                  ("run_id", "completed_at", "status_counts", "coverage_by_family",
                                                   "envelopes_written", "requests")}},
                      ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    Path(path).write_text(TEMPLATE.replace("__DATA__", data), encoding="utf-8")


TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Signalpost · Norwegian company profiles</title>
<style>
:root{--bg:#f6f7f9;--card:#fff;--ink:#14171f;--mute:#5d6575;--line:#e3e6ec;--acc:#123eaa;--ok:#147a4b;--warn:#a15c00;--bad:#b42318;--chip:#eef2fb}
@media (prefers-color-scheme:dark){:root{--bg:#0f1218;--card:#171b23;--ink:#e8ebf1;--mute:#9aa3b2;--line:#2a303b;--acc:#8fb0ff;--chip:#1f2633}}
*{box-sizing:border-box}body{margin:0;font:15px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:var(--bg);color:var(--ink)}
header{position:sticky;top:0;z-index:5;background:var(--card);border-bottom:1px solid var(--line);padding:10px 16px}
h1{font-size:17px;margin:0 0 6px}.sub{color:var(--mute);font-size:12px}
.bar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-top:8px}
input[type=search]{flex:1 1 240px;padding:9px 11px;border:1px solid var(--line);border-radius:9px;background:var(--bg);color:var(--ink);font-size:15px}
select,button{padding:8px 10px;border:1px solid var(--line);border-radius:9px;background:var(--card);color:var(--ink);font-size:14px;cursor:pointer}
.chip{border-radius:999px;padding:5px 10px;font-size:13px;background:var(--chip);border:1px solid transparent}.chip.on{border-color:var(--acc);color:var(--acc);font-weight:600}
main{display:grid;grid-template-columns:minmax(300px,420px) 1fr;gap:0;height:calc(100vh - 118px)}
#list{overflow:auto;border-right:1px solid var(--line)}#detail{overflow:auto;padding:18px 22px}
.item{display:flex;gap:10px;padding:11px 14px;border-bottom:1px solid var(--line);cursor:pointer}.item:hover,.item.sel{background:var(--chip)}
.item .nm{font-weight:600}.meta{color:var(--mute);font-size:12.5px}.badges{display:flex;gap:6px;flex-wrap:wrap;margin-top:4px}
.b{font-size:11.5px;padding:2px 7px;border-radius:6px;background:var(--chip);color:var(--mute)}.b.g{color:var(--ok)}.b.w{color:var(--warn)}.b.r{color:var(--bad)}
.meter{height:4px;background:var(--line);border-radius:4px;margin-top:6px}.meter i{display:block;height:4px;border-radius:4px;background:var(--acc)}
h2{font-size:20px;margin:0}h3{font-size:14px;text-transform:uppercase;letter-spacing:.04em;color:var(--mute);margin:22px 0 8px;display:flex;gap:8px;align-items:center}
.state{font-size:11px;text-transform:none;letter-spacing:0;padding:2px 7px;border-radius:6px;background:var(--chip)}
.summary{white-space:pre-line;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px;margin-top:12px}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:12px;overflow:hidden}
td,th{padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top;text-align:left;font-size:14px}th{width:34%;color:var(--mute);font-weight:500}
.src{border:0;background:none;color:var(--acc);padding:0 0 0 6px;font-size:12.5px;cursor:pointer}
.miss{color:var(--mute);font-style:italic}.stale{color:var(--warn);font-size:12px}
.ev{margin:6px 0 0;padding:8px 10px;border-left:3px solid var(--acc);background:var(--chip);border-radius:6px;font-size:12.5px;word-break:break-word}
.ev a{color:var(--acc)}.vl{margin:0;padding-left:18px}.vl li{margin:2px 0}.tl{list-style:none;padding:0;margin:0}.tl li{padding:7px 0;border-bottom:1px dashed var(--line)}.tl b{font-variant-numeric:tabular-nums}
.cmp{overflow:auto}.cmp th{width:auto}.empty{color:var(--mute);padding:40px;text-align:center}
.back{display:none}
@media (max-width:820px){header{position:static}main{grid-template-columns:1fr;height:auto}#list{border-right:0}#detail{display:none;padding:14px}
 body.open #list{display:none}body.open #detail{display:block}.back{display:inline-block;margin-bottom:10px}}
</style></head><body>
<header><h1>Signalpost · Norwegian company profiles</h1>
<div class="sub" id="runinfo"></div>
<div class="bar"><input type="search" id="q" placeholder="Search name, org number, city, industry…" aria-label="Search">
<select id="sort" aria-label="Sort"><option value="data">Most data found</option><option value="name">Name A–Z</option><option value="rev">Revenue</option><option value="emp">Employees</option><option value="jobs">Open jobs</option></select>
<button id="cmpbtn" title="Compare selected companies">Compare (0)</button></div>
<div class="bar" id="filters"></div></header>
<main><div id="list" role="list"></div><div id="detail"><div class="empty">Select a company to see its profile and the evidence behind every fact.</div></div></main>
<script id="data" type="application/json">__DATA__</script>
<script>
const D=JSON.parse(document.getElementById('data').textContent);const C=D.cards;const R=D.report||{};
const $=s=>document.querySelector(s);const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmtM=(a,c)=>{if(a==null)return'–';const n=Math.abs(a),s=a<0?'-':'';const v=n>=1e9?(n/1e9).toFixed(1)+' bn':n>=1e6?(n/1e6).toFixed(1)+' m':n>=1e3?(n/1e3).toFixed(0)+' k':n.toFixed(0);return s+(c||'NOK')+' '+v};
$('#runinfo').textContent=`Run ${R.run_id||''} · ${C.length} companies · completed ${R.completed_at||''} · every fact links to its source`;
const F=[['web','Verified website',c=>!!c.website],['hiring','Hiring',c=>c.jobs>0],['fin','Has accounts',c=>c.revenue!=null||c.net_result!=null],['chg','Changed since last run',c=>(c.changes||[]).some(x=>x.material)],['flag','Not active',c=>c.registry_status&&c.registry_status!=='active']];
const on=new Set();let sel=null;const cmp=new Set();
$('#filters').innerHTML=F.map(f=>`<button class="chip" data-f="${f[0]}">${f[1]}</button>`).join('');
$('#filters').onclick=e=>{const k=e.target.dataset.f;if(!k)return;on.has(k)?on.delete(k):on.add(k);e.target.classList.toggle('on');draw()};
$('#q').oninput=draw;$('#sort').onchange=draw;
function rows(){const q=$('#q').value.trim().toLowerCase();let r=C.filter(c=>!q||[c.name,c.brand,c.org,c.city,c.industry].join(' ').toLowerCase().includes(q));
 for(const k of on){const f=F.find(x=>x[0]===k);r=r.filter(f[2])}
 const s=$('#sort').value;const by={data:(a,b)=>b.completeness-a.completeness||a.name.localeCompare(b.name),name:(a,b)=>a.name.localeCompare(b.name),rev:(a,b)=>(b.revenue??-1e99)-(a.revenue??-1e99),emp:(a,b)=>(b.employees??-1)-(a.employees??-1),jobs:(a,b)=>(b.jobs??-1)-(a.jobs??-1)}[s];return r.sort(by)}
function draw(){const r=rows();$('#list').innerHTML=r.length?r.slice(0,600).map(c=>`<div class="item ${sel===c.org?'sel':''}" role="listitem" data-o="${c.org}">
<input type="checkbox" aria-label="Compare" data-c="${c.org}" ${cmp.has(c.org)?'checked':''}>
<div style="flex:1;min-width:0"><div class="nm">${esc(c.name)}</div><div class="meta">${esc(c.org)} · ${esc(c.city||'')} · ${esc(c.industry||'')}</div>
<div class="badges">${c.status!=='available'?`<span class="b r">${esc(c.status)}</span>`:''}${c.registry_status&&c.registry_status!=='active'?`<span class="b r">${esc(c.registry_status)}</span>`:''}${c.revenue!=null?`<span class="b">${fmtM(c.revenue,c.currency)} rev ${c.year||''}</span>`:''}${c.website?'<span class="b g">website ✓</span>':''}${c.jobs>0?`<span class="b g">${c.jobs} job${c.jobs>1?'s':''}</span>`:''}${(c.changes||[]).some(x=>x.material)?'<span class="b w">changed</span>':''}</div>
<div class="meter" title="Share of key fields found"><i style="width:${Math.round(c.completeness*100)}%"></i></div></div></div>`).join(''):'<div class="empty">No companies match.</div>'}
$('#list').onclick=e=>{const cb=e.target.dataset.c;if(cb){cmp.has(cb)?cmp.delete(cb):(cmp.size<3&&cmp.add(cb));$('#cmpbtn').textContent=`Compare (${cmp.size})`;e.stopPropagation();draw();return}const it=e.target.closest('.item');if(it)show(it.dataset.o)};
function evBtn(c,f,id){return f.ev&&f.ev.length?`<button class="src" data-ev="${id}" aria-expanded="false">source</button><div hidden id="${id}">${f.ev.map(k=>{const e=c.E[k];return `<div class="ev"><b>${esc(e.s)}</b>${e.p?' (previous run)':''}<br><a href="${esc(e.u)}" target="_blank" rel="noopener noreferrer">${esc(e.u)}</a><br>Retrieved ${esc(e.t)} · sha256 ${esc(e.h)}… · ${esc(e.l||'')}<br><q>${esc(e.x)}</q></div>`}).join('')}</div>`:''}
function rowsFor(c,fields){return fields.filter(f=>c.facts[f]).map(f=>{const x=c.facts[f];const id='e'+c.org+f;return `<tr><th>${esc(x.label)}</th><td>${x.state==='available'?(x.text?esc(x.text):renderVal(f,x.value))+(x.stale?' <span class="stale">(kept from previous run)</span>':'')+evBtn(c,x,id):`<span class="miss">${esc(x.state.replace('_',' '))}${x.reason?': '+esc(x.reason):''}</span>`}${x.note&&x.state==='available'?`<div class="meta">${esc(x.note)}</div>`:''}</td></tr>`}).join('')}
function renderVal(f,v){if(v==null)return'';if(Array.isArray(v)){if(!v.length)return'none';const skip=['title','name','url','date','published','rank','kind','role_code','group','holder_type','platform','address','changes','type','evidence_ids'];
 return '<ul class="vl">'+v.slice(0,15).map(x=>{if(typeof x!=='object'||x===null)return `<li>${esc(x)}</li>`;const lead=x.date||x.published||'';const label=x.title||x.name||(x.changes&&x.changes.join('; '))||x.type||x.url||'';
 const main=x.url?`<a href="${esc(x.url)}" target="_blank" rel="noopener noreferrer">${esc(label)}</a>`:esc(label);
 const meta=Object.entries(x).filter(([k,y])=>y!=null&&typeof y!=='object'&&!skip.includes(k)).map(([k,y])=>esc(String(y))).join(' · ');
 const addr=x.address&&typeof x.address==='object'?[(x.address.lines||[]).join(' '),x.address.postal_code,x.address.city].filter(Boolean).join(', '):'';
 return `<li>${lead?`<b>${esc(lead)}</b> `:''}${x.platform?esc(x.platform)+': ':''}${main}${meta||addr?` <span class="meta">${[meta,esc(addr)].filter(Boolean).join(' · ')}</span>`:''}</li>`}).join('')+(v.length>15?`<li class="meta">… ${v.length-15} more</li>`:'')+'</ul>'}
 if(typeof v==='object'){if(v.items)return`${esc(v.total)} total`+renderVal(f,v.items);if(v.figures)return Object.entries(v.figures).map(([k,y])=>`${esc(k.replace(/_/g,' '))}: ${esc(fmtM(y,v.currency))}`).join('<br>')+`<div class="meta">${esc(v.period_start)} – ${esc(v.period_end)} · ${esc(v.accounts_type||'')}</div>`;
 return Object.entries(v).filter(([k,y])=>y!=null&&typeof y!=='object').map(([k,y])=>`${esc(k.replace(/_/g,' '))}: ${esc(y)}`).join('<br>')}return esc(v)}
const S=[['Company brief','company_brief',['legal_name','public_brand_name','organisation_form','registry_status','industry','business_description','website_description','registered_employees','founding_date','registration_date','vat_registered','business_register','share_capital','group_membership','former_names','institutional_sector','parent_unit']],
['Latest financials','latest_financials',['annual_accounts_latest','revenue','operating_result','profit_before_tax','net_result','equity','total_assets','total_liabilities','personnel_costs','revenue_change','annual_accounts_history','latest_filed_accounts_year']],
['Who runs it','who_runs_it',['chief_executive','board_chair','board_members','auditor','accountant','leadership']],
['Where it operates','where_it_operates',['business_address','postal_address','registered_workplaces','website_locations','registered_contact','website_contact']],
['Working here','working_here',['hiring_signal','job_postings','careers_page']],
['Online presence','online_presence',['official_website','registered_website','social_profiles']],
['Recent activity','recent_activity',['public_activity','registry_change_events','role_change_events','website_news']]];
function show(o){sel=o;const c=C.find(x=>x.org===o);document.body.classList.add('open');
 let h=`<button class="back" onclick="document.body.classList.remove('open')">← All companies</button><h2>${esc(c.name)}</h2><div class="meta">Org. nr ${esc(c.org)} · ${esc(c.form||'')} · ${esc(c.city||'')} · profile status: ${esc(c.status)}</div>`;
 if(c.summary)h+=`<div class="summary">${esc(c.summary)}</div>`;
 if((c.changes||[]).length)h+=`<h3>What changed since last run</h3><table>${c.changes.slice(0,30).map(x=>`<tr><th>${esc(x.type.replace(/_/g,' '))}</th><td>${esc(x.field.replace(/_/g,' '))}${x.material?' · <b>material</b>':''}<div class="meta">${esc(JSON.stringify(x.before)).slice(0,160)} → ${esc(JSON.stringify(x.after)).slice(0,160)}</div></td></tr>`).join('')}</table>`;
 for(const [t,k,fs] of S){const st=c.sections[k]||'';h+=`<h3>${t} <span class="state">${esc(st.replace('_',' '))}</span></h3><table>${rowsFor(c,fs)}</table>`}
 if(c.errors.length)h+=`<h3>Run notes</h3><ul>${c.errors.map(e=>`<li class="meta">${esc(e.source)}: ${esc(e.message)}</li>`).join('')}</ul>`;
 $('#detail').innerHTML=h;draw();window.scrollTo(0,0)}
$('#detail').onclick=e=>{const id=e.target.dataset.ev;if(!id)return;const el=document.getElementById(id);el.hidden=!el.hidden;e.target.setAttribute('aria-expanded',String(!el.hidden))};
$('#cmpbtn').onclick=()=>{if(!cmp.size)return;const cs=[...cmp].map(o=>C.find(x=>x.org===o));document.body.classList.add('open');
 const lines=[['Org. nr',c=>c.org],['Legal form',c=>c.form],['City',c=>c.city],['Industry',c=>c.industry],['Status',c=>c.registry_status],['Revenue',c=>c.revenue!=null?fmtM(c.revenue,c.currency)+' ('+c.year+')':'–'],['Net result',c=>c.net_result!=null?fmtM(c.net_result,c.currency):'–'],['Employees',c=>c.employees??'–'],['Website',c=>c.website||'–'],['Open jobs',c=>c.jobs??'–'],['Data found',c=>Math.round(c.completeness*100)+'%']];
 $('#detail').innerHTML=`<button class="back" onclick="document.body.classList.remove('open')">← All companies</button><h2>Compare</h2><div class="cmp"><table><tr><th></th>${cs.map(c=>`<th>${esc(c.name)}</th>`).join('')}</tr>${lines.map(([l,f])=>`<tr><th>${l}</th>${cs.map(c=>`<td>${esc(f(c))}</td>`).join('')}</tr>`).join('')}</table></div>`};
draw();
</script></body></html>
"""
