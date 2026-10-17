.PHONY: help install test lint format up down bad-release-errors bad-release-slow load clean infra-install infra-test synth deploy-base push deploy-service url status stop destroy-all

# ---- AWS settings (read from your AWS CLI config) ------------------------------
AWS_REGION ?= $(shell aws configure get region 2>/dev/null)
ACCOUNT     = $(shell aws sts get-caller-identity --query Account --output text 2>/dev/null)
REGISTRY    = $(ACCOUNT).dkr.ecr.$(AWS_REGION).amazonaws.com
IMAGE       = $(REGISTRY)/pesaguard
FAULT_MODE ?= none
# Run cdk with the infra virtualenv's python first on the PATH.
CDK         = cd infra && PATH="$(CURDIR)/infra/.venv/bin:$$PATH" cdk

help:            ## Show available commands
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-20s %s\n", $$1, $$2}'

# ---- Phase 1-2: app, local ------------------------------------------------------
install:         ## Create a virtualenv and install dev dependencies
	python3 -m venv .venv
	.venv/bin/pip install -r app/requirements-dev.txt

test:            ## Run unit tests with coverage
	cd app && ../.venv/bin/pytest

lint:            ## Check code style (app and infra)
	cd app && ../.venv/bin/ruff check . && ../.venv/bin/ruff format --check .
	cd infra && ../.venv/bin/ruff check . && ../.venv/bin/ruff format --check .

format:          ## Auto-fix code style (app and infra)
	cd app && ../.venv/bin/ruff check --fix . && ../.venv/bin/ruff format .
	cd infra && ../.venv/bin/ruff check --fix . && ../.venv/bin/ruff format .

up:              ## Start API + DynamoDB Local on http://localhost:8080
	docker compose up --build -d
	@echo "Waiting for the API..." && sleep 5 && curl -s http://localhost:8080/health && echo

down:            ## Stop everything
	docker compose down -v

bad-release-errors:  ## Run locally as a broken release (30% errors)
	APP_VERSION=v1.2.0-bad FAULT_MODE=error_rate=0.3 docker compose up --build -d

bad-release-slow:    ## Run locally as a slow release (1.5s per payment)
	APP_VERSION=v1.3.0-slow FAULT_MODE=latency_ms=1500 docker compose up --build -d

load:            ## Send steady payment traffic (needs k6). Add BASE_URL=... for AWS
	k6 run $(if $(BASE_URL),-e BASE_URL=$(BASE_URL)) load/payments_load.js

# ---- Phase 3: AWS ---------------------------------------------------------------
infra-install:   ## Create the infra virtualenv (CDK libraries)
	python3 -m venv infra/.venv
	infra/.venv/bin/pip install -r infra/requirements-dev.txt

infra-test:      ## Run infrastructure tests
	cd infra && .venv/bin/pytest

synth:           ## Generate CloudFormation and run security checks (no AWS changes)
	$(CDK) synth --quiet -c imageTag=$(or $(TAG),not-set)

deploy-base:     ## Deploy network, table, registry and GitHub deploy role
	$(CDK) deploy PesaGuardNetwork PesaGuardData PesaGuardEcr PesaGuardGithubOidc -c imageTag=not-set

push:            ## Build and push an image: make push TAG=v1.0.0 [FAULT_MODE=error_rate=0.3]
	@test -n "$(TAG)" || (echo "Usage: make push TAG=v1.0.0" && exit 1)
	docker build --build-arg APP_VERSION=$(TAG) --build-arg FAULT_MODE=$(FAULT_MODE) -t $(IMAGE):$(TAG) app
	aws ecr get-login-password --region $(AWS_REGION) | docker login --username AWS --password-stdin $(REGISTRY)
	docker push $(IMAGE):$(TAG)

deploy-service:  ## Deploy or update the service: make deploy-service TAG=v1.0.0
	@test -n "$(TAG)" || (echo "Usage: make deploy-service TAG=v1.0.0" && exit 1)
	$(CDK) deploy PesaGuardService -c imageTag=$(TAG)

url:             ## Print the public API address
	@aws cloudformation describe-stacks --stack-name PesaGuardService --query "Stacks[0].Outputs[?OutputKey=='ApiUrl'].OutputValue" --output text

status:          ## Show running tasks and the current deployment
	@aws ecs describe-services --cluster pesaguard --services pesaguard-api --query "services[0].{running:runningCount,desired:desiredCount,deployments:deployments[].{status:status,rollout:rolloutState,taskDef:taskDefinition}}"

stop:            ## End of session: delete the service + load balancer (the parts billed by the hour)
	$(CDK) destroy PesaGuardService --force -c imageTag=not-set

destroy-all:     ## Delete EVERYTHING in AWS, including the table and images
	$(CDK) destroy --all --force -c imageTag=not-set

clean:           ## Remove caches
	find . -name __pycache__ -prune -exec rm -rf {} +
	rm -rf app/.pytest_cache app/.ruff_cache app/.coverage infra/cdk.out
