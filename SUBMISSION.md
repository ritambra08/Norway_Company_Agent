# Submission (Signalpost, revised version)

## Before you email

1. `make test`: all tests should pass.
2. `make smoke`: runs a 100-company smoke test on the official universe, then a refresh rerun and `validate`. It writes to `submission/`.
   - Check that `submission/validate.txt` ends with `0 problem(s)`.
   - Check that `submission/run-report.json` shows `"one_terminal_envelope_per_input": true`.
   - Optional but recommended: `bash scripts/scale_test.sh` runs once on 1,000 fresh companies (the official
     batch size) into `scale/` and prints the runtime. It never touches `submission/`.
3. Commit `submission/` and push. Then run `git rev-parse HEAD`; that is the exact commit hash to submit.
4. Build the JBOX site (docs/JBOX.md), publish it, and put its URL in README.md and in the email. The JBOX deadline is 7 November, 23:59 IST.

## Email to submit@builderr.ai

Subject: `Signalpost Round 1 submission – revision`

```
Agent name: Norway Company Agent (Signalpost) v0.2.6
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
  defaults scale the request cap at 18 × companies + 200 (overridable via SIGNALPOST_MAX_REQUESTS).
Time budget: the agent always writes one envelope per input before its own deadline guard (default 40 minutes,
  60 s reserved for writing). If your run budget is shorter, set SIGNALPOST_DEADLINE_SECONDS to a little under it
  (for example 1500 for a 30-minute budget); the agent then stops collecting early and still returns every envelope,
  marking unchecked sources "not_checked" rather than dropping companies.
UI: offline viewer out/index.html; Signalpost site built with JBOX (JBOX bonus entry): <JBOX site URL>
Contact for results: <name, email>
```

Fill in the hash, the JBOX URL and your contact details.
