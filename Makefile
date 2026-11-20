.PHONY: help test coverage lint security run smoke docker-build container container-stop compose-up compose-down

APP := atm-dispenser
IN_APP := cd $(APP) && PYTHONPATH=src

help:            ## list targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | sed 's/:.*##/\t/'

test:            ## run all automated tests
	$(IN_APP) python3 -m unittest discover -v

coverage:        ## tests with coverage report
	$(IN_APP) coverage run -m unittest discover && coverage report

lint:            ## ruff + mypy
	$(IN_APP) ruff check . && mypy

security:        ## bandit + pip-audit
	$(IN_APP) bandit -r src/atm -c pyproject.toml && pip-audit -r requirements-dev.txt

run:             ## start the service on http://127.0.0.1:8080
	$(IN_APP) python3 -m atm.service

smoke:           ## smoke test a running service (URL=...)
	python3 $(APP)/tests/e2e/smoke_test.py $(or $(URL),http://localhost:8080)

docker-build: test  ## run tests, then build the image
	docker build -t atm-dispenser:local .

container:       ## test, build, start the hardened container, check it
	./scripts/run-container.sh

container-stop:  ## stop and remove the container
	./scripts/run-container.sh --stop

compose-up: test ## run tests, then start with docker compose
	docker compose up --build -d

compose-down:    ## stop docker compose
	docker compose down
