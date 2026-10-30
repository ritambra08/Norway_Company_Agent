# Smoke-test artefacts

`make smoke` (or `./scripts/smoke_test.sh`) writes the 100-company smoke-test result here. The brief requires it in the public artefact, so commit these files before you submit:

- `smoke-companies.jsonl`: the batch, selected from the official universe with the starter kit's selector
- `run-report.json`: the first run (requests, runtime, cost, coverage, one envelope per input)
- `run-report-refresh.json`: the second run on the same batch (refresh and change detection)
- `validate.txt`, `validate-refresh.txt`: self-validation with evidence replay (should end in `0 problem(s)`)
- `envelopes.jsonl.gz`, `profiles.jsonl`, `index.html`: the envelopes, the profile cards and the offline viewer
