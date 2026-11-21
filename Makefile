.PHONY: help version test shared-inventory coverage lint security run smoke docker-build container container-stop

APP := atm-dispenser
IN_APP := cd $(APP) &&

version:         ## print the build version (major.minor.datetime)
	@./scripts/version.sh

help:            ## list targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | sed 's/:.*##/\t/'

test:            ## run all automated tests
	$(IN_APP) python3 -m unittest discover -v

shared-inventory: ## several processes share one inventory file and never pay the same notes
	./scripts/shared-inventory-test.sh

coverage:        ## tests with coverage report
	$(IN_APP) coverage run -m unittest discover && coverage report

lint:            ## ruff (incl. security rules) + mypy
	$(IN_APP) ruff check . && mypy

security:        ## dependency audit (security lint rules run in "make lint")
	$(IN_APP) pip-audit -r requirements-dev.txt

run:             ## start the service on http://127.0.0.1:8080
	python3 $(APP)/src/service.py

smoke:           ## smoke test a running service (URL=...)
	python3 $(APP)/tests/e2e/smoke_test.py $(or $(URL),http://localhost:8080)

docker-build: test  ## run tests, then build and validate the versioned image
	VERSION=$$(./scripts/version.sh) && \
	docker build --build-arg VERSION=$$VERSION -t atm-dispenser:$$VERSION -t atm-dispenser:local . && \
	./scripts/validate-image.sh atm-dispenser:$$VERSION $$VERSION

container:       ## test, build, start the hardened container, check it
	./scripts/run-container.sh

container-stop:  ## stop and remove the container
	./scripts/run-container.sh --stop

