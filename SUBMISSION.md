# Submission checklist

| Item | How |
|---|---|
| 1,000+ profiles | `make sample N=1000` then `python3 -m norway_agent run --input companies.txt --out out --max-requests 6000`, then `make validate`. Submit `out/profiles.jsonl` (and `out/raw/` as evidence). Aim for ≥ 1,000 profiles with status `ok`; check `out/manifest.json`. |
| Repository link | push this folder to GitHub (`git init && git add -A && git commit -m "Norway company agent v0.1" && git remote add origin <url> && git push -u origin main`) |
| Exact commit hash | `git rev-parse HEAD` (run after the final commit; any later change means a new hash) |
| One command | `./run.sh companies.txt out` |
| Model/API details | No LLM. Public APIs only: `data.brreg.no/enhetsregisteret/api` and `data.brreg.no/regnskapsregisteret/regnskap` (free, keyless, NLOD licence). |
| Expected run costs | External API: **$0.00**. Daily test of 100 companies: ≤ ~500 requests, 1–2 minutes. Infrastructure: any machine with Python ≥ 3.10. |

Before submitting: run 5–10 real companies first and read `out/raw/` and `out/profiles/` (registry and accounts calls were confirmed live on three large companies; everything else on fixtures).
