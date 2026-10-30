BATCH ?= examples/sample-batch.jsonl
OUT ?= out
N ?= 100

.PHONY: run test validate smoke

# The one evaluator command (same as ./run.sh $(BATCH) $(OUT))
run:
	./run.sh $(BATCH) $(OUT)

test:
	python3 -I -m unittest discover -s . -t . -p "test_*.py"

validate:
	python3 -m norway_agent validate --output $(OUT)/envelopes.jsonl --organisations $(BATCH)

# 100-company smoke test on the official universe; results land in submission/
smoke:
	N=$(N) ./scripts/smoke_test.sh
