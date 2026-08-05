.DEFAULT_GOAL := help
SHELL := /bin/bash

PYTHON ?= python3
VENV := .venv
BIN := $(VENV)/bin
COMPOSE := docker compose -f infra/compose/docker-compose.yml

SERVICES := auth user merchant catalog
PACKAGES := libs/core services/auth services/user services/merchant services/catalog api-gateway

.PHONY: help
help: ## Show available targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| sort \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

# ------------------------------------------------------------------------------ setup

.PHONY: venv
venv: ## Create the virtual environment
	$(PYTHON) -m venv $(VENV)
	$(BIN)/pip install --quiet --upgrade pip wheel setuptools

.PHONY: install
install: venv ## Install every package in editable mode plus dev tooling
	$(BIN)/pip install --quiet $(foreach pkg,$(PACKAGES),-e $(pkg))
	$(BIN)/pip install --quiet pytest pytest-asyncio pytest-cov ruff mypy uvicorn[standard] aiokafka pyyaml

# ---------------------------------------------------------------------------- quality

.PHONY: lint
lint: ## Check formatting and lint rules
	$(BIN)/ruff check .

.PHONY: format
format: ## Apply safe lint fixes and format
	$(BIN)/ruff check . --fix
	$(BIN)/ruff format .

.PHONY: typecheck
typecheck: ## Run mypy over all source packages
	$(BIN)/mypy libs/core/src $(foreach svc,$(SERVICES),services/$(svc)/src) api-gateway/src

.PHONY: test
test: ## Run the full test suite
	$(BIN)/python -m pytest

.PHONY: test-unit
test-unit: ## Run only tests that need no external services
	$(BIN)/python -m pytest -m "not integration"

.PHONY: coverage
coverage: ## Run the suite with a coverage report
	$(BIN)/python -m pytest --cov --cov-report=term-missing --cov-report=xml

.PHONY: check
check: lint typecheck test ## Everything CI runs

# --------------------------------------------------------------------------- database

.PHONY: migrate
migrate: ## Apply every service's migrations
	$(BIN)/python scripts/migrate_all.py

.PHONY: migrate-down
migrate-down: ## Roll every service's migrations back to base
	$(BIN)/python scripts/migrate_all.py --downgrade --revision base

.PHONY: migration
migration: ## Autogenerate a migration: make migration service=auth rev=0002_auth msg="add x"
	@test -n "$(service)" || (echo "usage: make migration service=auth rev=0002_auth msg=\"add x\"" && exit 1)
	@test -n "$(rev)" || (echo "usage: make migration service=auth rev=0002_auth msg=\"add x\"" && exit 1)
	cd services/$(service) && ../../$(BIN)/alembic revision --autogenerate --rev-id "$(rev)" -m "$(msg)"

.PHONY: seed
seed: ## Load Abu Dhabi sample merchants, menus and a demo customer
	$(BIN)/python scripts/seed_local.py

# ------------------------------------------------------------------------------ local

.PHONY: up
up: ## Start the full local stack in Docker
	$(COMPOSE) up --build -d

.PHONY: down
down: ## Stop the local stack
	$(COMPOSE) down

.PHONY: clean-volumes
clean-volumes: ## Stop the stack and delete its data volumes
	$(COMPOSE) down -v

.PHONY: logs
logs: ## Follow logs from the local stack
	$(COMPOSE) logs -f

.PHONY: run-auth run-user run-merchant run-catalog run-gateway
run-auth: ## Run the auth service on the host
	$(BIN)/uvicorn marsool_auth.main:app --reload --port 8001
run-user: ## Run the user service on the host
	$(BIN)/uvicorn marsool_user.main:app --reload --port 8002
run-merchant: ## Run the merchant service on the host
	$(BIN)/uvicorn marsool_merchant.main:app --reload --port 8003
run-catalog: ## Run the catalog service on the host
	$(BIN)/uvicorn marsool_catalog.main:app --reload --port 8004
run-gateway: ## Run the API gateway on the host
	$(BIN)/uvicorn marsool_gateway.main:app --reload --port 8000

# --------------------------------------------------------------------------- contracts

.PHONY: openapi
openapi: ## Export every service's OpenAPI document to docs/openapi/
	$(BIN)/python scripts/export_openapi.py
