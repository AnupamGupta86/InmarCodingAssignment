.PHONY: help test shared-inventory dev-tools coverage lint security

APP := atm-dispenser
IN_APP := cd $(APP) &&

help:            ## list targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | sed 's/:.*##/\t/'

test:            ## run all automated tests
	$(IN_APP) python3 -m unittest discover -v

shared-inventory: ## several processes share one inventory file and never pay the same notes
	./scripts/shared-inventory-test.sh

dev-tools:       ## check that the dev tools are installed in the active Python
	@python3 -c "import ruff, mypy, coverage, pip_audit" 2>/dev/null || { \
	  echo "Dev tools not found. Set them up once in a virtual environment:"; \
	  echo "  python3 -m venv .venv && source .venv/bin/activate"; \
	  echo "  python3 -m pip install -r atm-dispenser/requirements-dev.txt"; \
	  echo "Later, just run: source .venv/bin/activate"; \
	  exit 1; }

coverage: dev-tools  ## tests with coverage report
	$(IN_APP) python3 -m coverage run -m unittest discover && python3 -m coverage report

lint: dev-tools      ## ruff (incl. security rules) + mypy
	$(IN_APP) python3 -m ruff check . && python3 -m mypy

security: dev-tools  ## dependency audit (security lint rules run in "make lint")
	$(IN_APP) python3 -m pip_audit -r requirements-dev.txt
