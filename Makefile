<<<<<<< HEAD
TRUNK_DSN  ?= postgresql://trunk:trunk@localhost:5434/trunk
NERODE_DSN ?= postgresql://nerode:nerode@localhost:5435/nerode

.PHONY: up down apply apply-trunkit apply-nerode check install dev-install test lint build

## Start both PostgreSQL instances via Docker Compose
up:
	docker compose up -d db-trunkit db-nerode
=======
DSN ?= postgresql://trunk:trunk@localhost:5434/trunk

.PHONY: up down apply check reset install

## Start PostgreSQL via Docker Compose
up:
	docker compose up -d db
>>>>>>> origin/main

## Stop and remove containers
down:
	docker compose down

<<<<<<< HEAD
## Apply Trunkit (calx/kan/curry/cert) schemas — idempotent
apply-trunkit:
	@for f in $$(ls src/calx/sql/*.sql | sort); do \
		echo "  $$f"; \
		psql "$(TRUNK_DSN)" -f "$$f" -q; \
	done
	@echo "Trunkit schema applied."

## Apply Nerode (automata/session/porter) schemas — idempotent
apply-nerode:
	@for f in $$(ls src/nerode/sql/*.sql | sort); do \
		echo "  $$f"; \
		psql "$(NERODE_DSN)" -f "$$f" -q; \
	done
	@echo "Nerode schema applied."

## Apply all schemas for both databases
apply: apply-trunkit apply-nerode

## Trunkit smoke check: populate integers and run reflexive closure
check-trunkit:
	python tools/kan_in_kan.py

## Nerode smoke check: build a minimal DFA from a*b+ and run it
check-nerode:
	nerode build --regex "a*b+" --dsn "$(NERODE_DSN)"
	nerode run  --input "aaab"   --dsn "$(NERODE_DSN)" --id 1

## Run all checks
check: check-trunkit check-nerode

## Full local bootstrap: up -> apply -> check
install: up
	@echo "Waiting for databases to be ready..."
=======
## Apply all SQL schemas in order (idempotent)
apply:
	@for f in $$(ls src/calx/sql/*.sql | sort); do \
		echo "  $$f"; \
		psql $(DSN) -f "$$f" -q; \
	done
	@echo "Done. Run 'make check' to verify."

## Populate integers and run reflexive closure + cert attestation
check:
	python tools/kan_in_kan.py

## Attest formal-tier proof artifacts
attest:
	python tools/cert_formal.py

## Full local bootstrap: up → apply → check
install: up
	@echo "Waiting for db to be ready..."
>>>>>>> origin/main
	@sleep 3
	$(MAKE) apply
	$(MAKE) check

<<<<<<< HEAD
## Install Python packages in editable/dev mode
dev-install:
	pip install -e ".[dev]"

## Run tests
test:
	pytest -v

## Network tests (real HTTP — weather, tickers, HN)
test-network:
	pytest tests/test_sources.py -m network -v

## Lint
lint:
	ruff check src tests

## Build wheel
build:
	python -m build

## Drop Trunkit schemas and start fresh (destructive)
reset-trunkit:
	psql "$(TRUNK_DSN)" -c "DROP SCHEMA IF EXISTS cert, kan, curry, calx CASCADE;"
	$(MAKE) apply-trunkit
	$(MAKE) check-trunkit

## Drop Nerode schemas and start fresh (destructive)
reset-nerode:
	psql "$(NERODE_DSN)" -c "DROP SCHEMA IF EXISTS nerode CASCADE;"
	$(MAKE) apply-nerode
=======
## Drop all Trunkit schemas and start fresh (destructive)
reset:
	psql $(DSN) -c "DROP SCHEMA IF EXISTS cert, kan, curry, calx CASCADE;"
	$(MAKE) apply
	$(MAKE) check
>>>>>>> origin/main
