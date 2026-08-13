PYTHON ?= python3
OUT ?= out
DATA ?= data/storefront.csv

.DEFAULT_GOAL := help

.PHONY: help demo data report test samples clean

help: ## Show the available targets
	@grep -hE '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[1m%-10s\033[0m %s\n", $$1, $$2}'

demo: ## Generate data, compute facts, write and validate the report (no API key)
	$(PYTHON) -m veritas demo --data $(DATA) --out $(OUT)

data: ## Regenerate the synthetic dataset only
	$(PYTHON) -m veritas generate --out $(DATA)

report: ## Re-run the report over existing data
	$(PYTHON) -m veritas run --data $(DATA) --out $(OUT)

test: ## Run the test suite (stdlib unittest; pytest also works)
	$(PYTHON) -m unittest discover -s tests -t . -b

samples: demo ## Refresh the checked-in sample report and audit trail
	cp $(OUT)/report.md samples/report.md
	cp $(OUT)/audit.json samples/audit.json

clean: ## Remove generated data and output
	rm -rf $(OUT) $(DATA) .pytest_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
