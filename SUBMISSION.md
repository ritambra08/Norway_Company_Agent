# Submission (Signalpost, revised version)

## Before you email

1. `make test`: all tests should pass.
2. `make smoke`: runs a 100-company smoke test on the official universe, then a refresh rerun and `validate`. It writes to `submission/`.
   - Check that `submission/validate.txt` ends with `0 problem(s)`.
   - Check that `submission/run-report.json` shows `"one_terminal_envelope_per_input": true`.
3. Commit `submission/` and push. Then run `git rev-parse HEAD`; that is the exact commit hash to submit.
4. Optional: build the JBOX app (docs/JBOX.md), import `submission/profiles.jsonl`, and publish it.

## Email to submit@builderr.ai

Subject: `Signalpost Round 1 submission – revision`

```
Agent name: Norway Company Agent (Signalpost) v0.2.1
Repository URL: https://github.com/ritambra08/Norway_Company_Agent
Exact commit hash: <git rev-parse HEAD>
100-company smoke-test result: submission/run-report.json, submission/validate.txt, submission/envelopes.jsonl.gz, submission/index.html (in the repository at that commit)
One-command run instruction (Python 3.10+, no install step, no API keys):
  ./run.sh <batch.jsonl> <out-dir>
  (equivalent: python3 -m norway_agent run --organisations <batch.jsonl> --output <out>/envelopes.jsonl --report <out>/run-report.json --run-id <id>)
  Output: <out>/envelopes.jsonl, one terminal envelope per input (OUTPUT_CONTRACT fields + six availability states).
  Refresh: rerun into the same folder, or pass --previous <old envelopes.jsonl>; typed material changes appear in "changes".
Models / APIs / licences:
  No LLM or model. Brønnøysundregistrene Enhetsregisteret + Regnskapsregisteret open APIs (NLOD 2.0);
  NAV job vacancy feed (pam-stilling-feed.nav.no; NAV API terms; public token or NAV_FEED_TOKEN env var);
  companies' own websites (robots.txt honoured). Details: docs/SOURCES_AND_RIGHTS.md.
Expected cost per official run: $0.00 third-party. About 4–6 register requests per company plus 15–80 NAV feed pages per batch plus website requests;
  defaults scale the request cap at 18 × companies + 200 (overridable via SIGNALPOST_MAX_REQUESTS / SIGNALPOST_DEADLINE_SECONDS); 40-minute deadline guard.
UI: offline viewer out/index.html; hosted app built with JBOX: <JBOX app URL>
Contact for results: <name, email>
```

Fill in the hash, the JBOX URL (or remove that line) and your contact details.
