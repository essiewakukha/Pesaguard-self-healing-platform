.PHONY: help install test lint format run up down bad-release-errors bad-release-slow load clean

help:            ## Show available commands
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-20s %s\n", $$1, $$2}'

install:         ## Create a virtualenv and install dev dependencies
	python3 -m venv .venv
	.venv/bin/pip install -r app/requirements-dev.txt

test:            ## Run unit tests with coverage
	cd app && ../.venv/bin/pytest

lint:            ## Check code style
	cd app && ../.venv/bin/ruff check . && ../.venv/bin/ruff format --check .

format:          ## Auto-fix code style
	cd app && ../.venv/bin/ruff check --fix . && ../.venv/bin/ruff format .

up:              ## Start API + DynamoDB Local on http://localhost:8080
	docker compose up --build -d
	@echo "Waiting for the API..." && sleep 5 && curl -s http://localhost:8080/health && echo

down:            ## Stop everything
	docker compose down -v

bad-release-errors:  ## Run locally as a broken release (30% errors)
	APP_VERSION=v1.2.0-bad FAULT_MODE=error_rate=0.3 docker compose up --build -d

bad-release-slow:    ## Run locally as a slow release (1.5s per payment)
	APP_VERSION=v1.3.0-slow FAULT_MODE=latency_ms=1500 docker compose up --build -d

load:            ## Send steady payment traffic (needs k6 installed)
	k6 run load/payments_load.js

clean:           ## Remove caches
	find . -name __pycache__ -prune -exec rm -rf {} +
	rm -rf app/.pytest_cache app/.ruff_cache app/.coverage