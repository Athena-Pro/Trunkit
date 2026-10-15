TRUNK_DSN  ?= postgresql://trunk:trunk@localhost:5434/trunk
NERODE_DSN ?= postgresql://nerode:nerode@localhost:5435/nerode

.PHONY: up down apply apply-trunkit apply-nerode check check-trunkit check-nerode \
        install dev-install test test-network lint build reset-trunkit reset-nerode

## Start both PostgreSQL instances via Docker Compose
up:
	docker compose up -d db-trunkit db-nerode

## Stop and remove containers
down:
	docker compose down

## Apply Trunkit (calx/kan/curry/cert) schemas — idempotent
## Use the package loader so 2- and 3-digit migrations share one tested order.
apply-trunkit:
	trunkit --dsn "$(TRUNK_DSN)" init

## Apply Nerode (automata/session/porter) schemas — idempotent
## Same reason as apply-trunkit: nerode.db.SCHEMA_FILES is the ordering
## authority. Plain `sort` happens to be correct only while no file reaches
## three digits, and it cannot express that the letter-prefixed phases (A*/B*/C*)
## are dependency-ordered rather than alphabetical.
apply-nerode:
	nerode --dsn "$(NERODE_DSN)" close --apply

## Apply all schemas for both databases
apply: apply-trunkit apply-nerode

## Trunkit smoke check: exact arithmetic and installed certificate methods
check-trunkit:
	python -m calx.smoke --only trunkit --trunk-dsn "$(TRUNK_DSN)"

## Nerode smoke check: DFA execution and certificate-backed Porter handoff
check-nerode:
	python -m calx.smoke --only nerode --nerode-dsn "$(NERODE_DSN)"

## Run all checks
check: check-trunkit check-nerode

## Full local bootstrap: up -> apply -> check
install: up
	trunkit --dsn "$(TRUNK_DSN)" quickstart --nerode-dsn "$(NERODE_DSN)"
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
