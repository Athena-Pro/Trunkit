DSN ?= postgresql://trunk:trunk@localhost:5434/trunk

.PHONY: up down apply check reset install

## Start PostgreSQL via Docker Compose
up:
	docker compose up -d db

## Stop and remove containers
down:
	docker compose down

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
	@sleep 3
	$(MAKE) apply
	$(MAKE) check

## Drop all Trunkit schemas and start fresh (destructive)
reset:
	psql $(DSN) -c "DROP SCHEMA IF EXISTS cert, kan, curry, calx CASCADE;"
	$(MAKE) apply
	$(MAKE) check
