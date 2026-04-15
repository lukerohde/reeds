.DEFAULT_GOAL := help

# ── Load .env into Make ───────────────────────────────────────────────────────
ifneq (,$(wildcard .env))
  include .env
  export
endif

# GitHub Actions sets CI=true → skips confirmation prompts
PULUMI_YES := $(if $(CI),--yes,)

# Region where reeds infra lives — read from Pulumi config, not host env.
# This avoids mismatch when AWS_DEFAULT_REGION in .env or shell differs from
# the region where infra was deployed.
INFRA_REGION := $(shell grep -m1 'aws:region:' infra/pulumi/Pulumi.prod.yaml 2>/dev/null | awk '{print $$2}')

# ── Help ──────────────────────────────────────────────────────────────────────
.PHONY: help
help: ## Show available targets
	@grep -E '^[a-zA-Z_/-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-28s\033[0m %s\n", $$1, $$2}'

# ── Lambda — local invocation ─────────────────────────────────────────────────
.PHONY: test-feed
test-feed: ## Test a feed URL. Usage: make test-feed FEED=https://example.com/feed.xml
	@test -n "$(FEED)" || { echo "❌  Usage: make test-feed FEED=https://example.com/feed.xml"; exit 1; }
	@docker compose run --rm crawler python test_feed.py $(FEED)

.PHONY: test
test: ## Run unit tests
	@docker compose run --rm crawler python -m pytest test_handler.py -v

.PHONY: crawl
crawl: ## Run crawler Lambda locally (RSS feeds → DynamoDB)
	@test -n "$(DYNAMODB_TABLE)" || { echo "❌  DYNAMODB_TABLE not set in .env"; exit 1; }
	@docker compose run --rm \
		-e DYNAMODB_TABLE=$(DYNAMODB_TABLE) \
		-e AWS_DEFAULT_REGION=$(INFRA_REGION) \
		crawler \
		python -c "import json; from handler import handler; print(json.dumps(handler({}, None), indent=2))"

.PHONY: digest
digest: ## Run digest Lambda locally (DynamoDB → summary HTML → S3)
	@test -n "$(DYNAMODB_TABLE)"    || { echo "❌  DYNAMODB_TABLE not set in .env"; exit 1; }
	@test -n "$(ANTHROPIC_API_KEY)" || { echo "❌  ANTHROPIC_API_KEY not set in .env"; exit 1; }
	@BUCKET=$${BUCKET_NAME:-$$(docker compose run --rm -T pulumi stack output reeds_bucket 2>/dev/null | tail -1)}; \
	CFID=$${CF_DISTRIBUTION_ID:-$$(docker compose run --rm -T pulumi stack output reeds_distribution_id 2>/dev/null | tail -1)}; \
	test -n "$$BUCKET" || { echo "❌  Could not determine bucket — run 'make infra-up' first"; exit 1; }; \
	docker compose run --rm \
		-e DYNAMODB_TABLE=$(DYNAMODB_TABLE) \
		-e BUCKET_NAME=$$BUCKET \
		-e CF_DISTRIBUTION_ID=$$CFID \
		-e ANTHROPIC_API_KEY=$(ANTHROPIC_API_KEY) \
		-e AWS_DEFAULT_REGION=$(INFRA_REGION) \
		digester \
		python -c "import json, sys; from handler import handler; print(json.dumps(handler({}, None), indent=2))"

.PHONY: reset-all
reset-all: ## ⚠️  Delete ALL articles from DDB and re-crawl (use after schema changes)
	@test -n "$(DYNAMODB_TABLE)" || { echo "❌  DYNAMODB_TABLE not set in .env"; exit 1; }
	@docker compose run --rm \
		-e DYNAMODB_TABLE=$(DYNAMODB_TABLE) \
		-e AWS_DEFAULT_REGION=$(INFRA_REGION) \
		crawler python reset_all.py

.PHONY: redigest
redigest: ## Re-run today's digest (unserve today's articles then re-digest)
	$(MAKE) reset-today
	$(MAKE) digest

.PHONY: reset-today
reset-today: ## Unserve today's articles so digest can be re-run
	@test -n "$(DYNAMODB_TABLE)" || { echo "❌  DYNAMODB_TABLE not set in .env"; exit 1; }
	@docker compose run --rm \
		-e DYNAMODB_TABLE=$(DYNAMODB_TABLE) \
		-e AWS_DEFAULT_REGION=$(INFRA_REGION) \
		crawler python reset_today.py

.PHONY: dev
dev: ## Preview digest HTML locally — uses LocalStack DDB, no S3 upload, opens in browser
	@test -n "$(ANTHROPIC_API_KEY)" || { echo "❌  ANTHROPIC_API_KEY not set in .env"; exit 1; }
	@docker compose run --rm \
		-v /tmp:/tmp \
		-e DYNAMODB_TABLE=reeds-articles \
		-e BUCKET_NAME=reeds-local \
		-e ANTHROPIC_API_KEY=$(ANTHROPIC_API_KEY) \
		-e AWS_DEFAULT_REGION=eu-west-1 \
		-e AWS_ACCESS_KEY_ID=test \
		-e AWS_SECRET_ACCESS_KEY=test \
		-e AWS_ENDPOINT_URL=http://localstack:4566 \
		-e DIGEST_DRY_RUN=1 \
		digester \
		python -c "import json, sys; from handler import handler; r = handler({}, None); print(json.dumps(r, indent=2))"
	@open /tmp/reeds-digest-preview.html 2>/dev/null || echo "→ open /tmp/reeds-digest-preview.html in your browser"

# ── LocalStack — offline dev without real AWS ─────────────────────────────────
.PHONY: local-up
local-up: ## Start LocalStack and initialise DynamoDB table + S3 bucket
	docker compose up -d localstack
	@echo "⏳  Waiting for LocalStack…"
	@until docker compose exec localstack curl -sf http://localhost:4566/_localstack/health | grep -q '"dynamodb": "available"'; do sleep 1; done
	@echo "✅  LocalStack ready"
	@docker compose run --rm \
		-e AWS_ACCESS_KEY_ID=test \
		-e AWS_SECRET_ACCESS_KEY=test \
		-e AWS_DEFAULT_REGION=eu-west-1 \
		-e AWS_ENDPOINT_URL=http://localstack:4566 \
		awscli dynamodb create-table \
		--table-name reeds-articles \
		--attribute-definitions AttributeName=url,AttributeType=S \
		--key-schema AttributeName=url,KeyType=HASH \
		--billing-mode PAY_PER_REQUEST 2>/dev/null \
		&& echo "✅  DynamoDB table created" || echo "ℹ️   DynamoDB table already exists"
	@docker compose run --rm \
		-e AWS_ACCESS_KEY_ID=test \
		-e AWS_SECRET_ACCESS_KEY=test \
		-e AWS_DEFAULT_REGION=eu-west-1 \
		-e AWS_ENDPOINT_URL=http://localstack:4566 \
		awscli s3 mb s3://reeds-local 2>/dev/null \
		&& echo "✅  S3 bucket created" || echo "ℹ️   S3 bucket already exists"

.PHONY: local-reset
local-reset: ## Clear status/summary/served_date from all local articles (re-run transform from scratch)
	@docker compose run --rm \
		-e DYNAMODB_TABLE=reeds-articles \
		-e AWS_DEFAULT_REGION=eu-west-1 \
		-e AWS_ACCESS_KEY_ID=test \
		-e AWS_SECRET_ACCESS_KEY=test \
		-e AWS_ENDPOINT_URL=http://localstack:4566 \
		crawler python local_reset.py

.PHONY: local-crawl
local-crawl: ## Fetch RSS feeds → LocalStack DynamoDB (no real AWS)
	@docker compose run --rm \
		-e DYNAMODB_TABLE=reeds-articles \
		-e AWS_DEFAULT_REGION=eu-west-1 \
		-e AWS_ACCESS_KEY_ID=test \
		-e AWS_SECRET_ACCESS_KEY=test \
		-e AWS_ENDPOINT_URL=http://localstack:4566 \
		crawler \
		python -c "import json; from handler import handler; print(json.dumps(handler({}, None), indent=2))"

.PHONY: test-integration
test-integration: ## Run digest integration tests against LocalStack (make local-up first)
	@docker compose ps localstack 2>/dev/null | grep -qE "Up|running" \
		|| { echo "❌  LocalStack not running — run 'make local-up' first"; exit 1; }
	@docker compose run --rm \
		-e AWS_ACCESS_KEY_ID=test -e AWS_SECRET_ACCESS_KEY=test \
		-e AWS_DEFAULT_REGION=eu-west-1 -e AWS_ENDPOINT_URL=http://localstack:4566 \
		awscli dynamodb create-table \
		--table-name reeds-articles \
		--attribute-definitions AttributeName=url,AttributeType=S \
		--key-schema AttributeName=url,KeyType=HASH \
		--billing-mode PAY_PER_REQUEST 2>/dev/null || true
	@docker compose run --rm \
		-e AWS_ACCESS_KEY_ID=test -e AWS_SECRET_ACCESS_KEY=test \
		-e AWS_DEFAULT_REGION=eu-west-1 -e AWS_ENDPOINT_URL=http://localstack:4566 \
		awscli s3 mb s3://reeds-local 2>/dev/null || true
	@docker compose run --rm \
		-v /tmp:/tmp \
		-e DYNAMODB_TABLE=reeds-articles \
		-e BUCKET_NAME=reeds-local \
		-e ANTHROPIC_API_KEY=$(ANTHROPIC_API_KEY) \
		-e AWS_DEFAULT_REGION=eu-west-1 \
		-e AWS_ACCESS_KEY_ID=test \
		-e AWS_SECRET_ACCESS_KEY=test \
		-e AWS_ENDPOINT_URL=http://localstack:4566 \
		-e DIGEST_DRY_RUN=1 \
		digester \
		python -m pytest test_integration.py -v

# ── Deploy (static assets only — digest HTML is written by Lambda) ────────────
.PHONY: deploy
deploy: ## Sync public/ assets to S3 + invalidate CloudFront
	@BUCKET=$${SITE_BUCKET:-$$(docker compose run --rm -T pulumi stack output reeds_bucket 2>/dev/null | tail -1)}; \
	CFID=$${CF_DISTRIBUTION_ID:-$$(docker compose run --rm -T pulumi stack output reeds_distribution_id 2>/dev/null | tail -1)}; \
	test -n "$$BUCKET" || { echo "❌  Could not determine SITE_BUCKET — run 'make infra-up' first"; exit 1; }; \
	echo "→ Deploying public/ to $$BUCKET"; \
	docker compose run --rm awscli s3 sync /app/public/ s3://$$BUCKET --delete --exclude '.DS_Store'; \
	if [ -n "$$CFID" ]; then \
		docker compose run --rm awscli cloudfront create-invalidation --distribution-id $$CFID --paths '/*'; \
	fi

# ── Pulumi — reeds infra (S3 + CloudFront + DynamoDB + Lambda + EventBridge) ──
.PHONY: build-lambdas
build-lambdas: ## Install Lambda pip deps into backend/*/packages/ (auto-run by infra-up/preview)
	docker run --rm --platform linux/amd64 -v "$(CURDIR)":/app python:3.12-slim \
		sh -c "pip install -q -r /app/backend/crawler/requirements.txt -t /app/backend/crawler/packages/ --upgrade \
		    && pip install -q -r /app/backend/digest/requirements.txt  -t /app/backend/digest/packages/  --upgrade"
	@test -d backend/crawler/packages/yaml     || { echo "❌  build-lambdas: yaml missing from crawler"; exit 1; }
	@test -d backend/crawler/packages/feedparser || { echo "❌  build-lambdas: feedparser missing from crawler"; exit 1; }
	@test -d backend/digest/packages/yaml      || { echo "❌  build-lambdas: yaml missing from digest"; exit 1; }
	@test -d backend/digest/packages/anthropic || { echo "❌  build-lambdas: anthropic missing from digest"; exit 1; }
	@echo "✅  Lambda packages verified"

.PHONY: infra-preview
infra-preview: build-lambdas ## Preview infra changes
	docker compose build pulumi
	docker compose run --rm pulumi preview

.PHONY: infra-up
infra-up: build-lambdas ## Apply infra changes
	docker compose build pulumi
	docker compose run --rm pulumi up $(PULUMI_YES)

.PHONY: infra-destroy
infra-destroy: ## Destroy reeds infra ⚠️  careful
	docker compose run --rm pulumi destroy $(PULUMI_YES)

.PHONY: infra-outputs
infra-outputs: ## Show stack outputs (bucket name, CF distribution ID, table)
	docker compose run --rm pulumi stack output

# ── GitHub CLI (Docker fallback) ──────────────────────────────────────────────
.PHONY: gh
gh: ## Run gh CLI via Docker. Usage: make gh ARGS='repo list'
	docker compose run --rm gh gh $(ARGS)
