TRUNK_DSN  ?= postgresql://trunk:trunk@localhost:5434/trunk
NERODE_DSN ?= postgresql://nerode:nerode@localhost:5435/nerode

.PHONY: up down apply apply-trunkit apply-nerode check check-trunkit check-nerode reset reset-trunkit reset-nerode install dev-install test lint build attest

## Start both PostgreSQL instances via Docker Compose
up:
	docker compose up -d db-trunkit db-nerode

## Stop and remove containers
down:
	docker compose down

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

## Trunkit smoke check: populate integers and run reflexive closure + cert attestation
check-trunkit:
	python tools/kan_in_kan.py

## Nerode smoke check: build a minimal DFA from a*b+ and run it
check-nerode:
	nerode build --regex "a*b+" --dsn "$(NERODE_DSN)"
	nerode run  --input "aaab"   --dsn "$(NERODE_DSN)" --id 1

## Run all checks
check: check-trunkit check-nerode

## Attest formal-tier proof artifacts
attest:
	python tools/cert_formal.py

## Full local bootstrap: up -> apply -> check
install: up
	@echo "Waiting for databases to be ready..."
	@sleep 3
	$(MAKE) apply
	$(MAKE) check

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

## Drop all schemas and start fresh (destructive)
reset: reset-trunkit reset-nerode
