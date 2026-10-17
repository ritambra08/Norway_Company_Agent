INPUT ?= companies.txt
OUT ?= out
N ?= 1000

.PHONY: run test validate sample

# THE one command: build profiles for every org number in $(INPUT)
run:
	python3 -m norway_agent run --input $(INPUT) --out $(OUT)

test:
	python3 -I -m unittest discover -s . -t . -p "test_*.py"

validate:
	python3 -m norway_agent validate --out $(OUT)

# Draw $(N) random org numbers from the official bulk file (one download)
sample:
	python3 scripts/sample_orgnrs.py --n $(N) --out $(INPUT)
