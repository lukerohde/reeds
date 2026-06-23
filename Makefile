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
.PHONY: show-candidates
show-candidates: ## Show all relevant unserved articles and their summaries (local dev)
	@docker compose ps localstack 2>/dev/null | grep -qE "Up|running" \
		|| { echo "❌  LocalStack not running — run 'make local-up' first"; exit 1; }
	@docker compose run --rm \
		-e DYNAMODB_TABLE=reeds-articles \
		-e AWS_DEFAULT_REGION=eu-west-1 \
		-e AWS_ACCESS_KEY_ID=test \
		-e AWS_SECRET_ACCESS_KEY=test \
		-e AWS_ENDPOINT_URL=http://localstack:4566 \
		crawler python scripts/show_candidates.py

.PHONY: diagnose-author
diagnose-author: ## Show DDB stats for an author. Usage: make diagnose-author AUTHOR="Simon Willison"
	@test -n "$(AUTHOR)"         || { echo '❌  Usage: make diagnose-author AUTHOR="Author Name"'; exit 1; }
	@test -n "$(DYNAMODB_TABLE)" || { echo "❌  DYNAMODB_TABLE not set in .env"; exit 1; }
	@docker compose run --rm \
		-e DYNAMODB_TABLE=$(DYNAMODB_TABLE) \
		-e AWS_DEFAULT_REGION=$(INFRA_REGION) \
		-e "AUTHOR=$(AUTHOR)" \
		crawler python scripts/diagnose_author.py

.PHONY: test-feed
test-feed: ## Test a feed URL. Usage: make test-feed FEED=https://example.com/feed.xml
	@test -n "$(FEED)" || { echo "❌  Usage: make test-feed FEED=https://example.com/feed.xml"; exit 1; }
	@docker compose run --rm crawler python scripts/test_feed.py $(FEED)

.PHONY: add-youtuber
add-youtuber: ## Resolve a YouTube handle/URL to its channel ID and add it to config. Usage: make add-youtuber HANDLE=@buildwithdc
	@test -n "$(HANDLE)" || { echo "❌  Usage: make add-youtuber HANDLE=@handle  (or a channel URL / UC… ID)"; exit 1; }
	@docker compose run --rm crawler python scripts/add_youtuber.py "$(HANDLE)"

.PHONY: logs
logs: ## Tail a Lambda's CloudWatch logs. Usage: make logs FN=crawler|digest [SINCE=1h]  (needs the reeds-logs-read IAM grant)
	@test -n "$(FN)" || { echo "❌  Usage: make logs FN=crawler|digest [SINCE=1h]"; exit 1; }
	@docker compose run --rm --entrypoint sh -e AWS_DEFAULT_REGION=$(INFRA_REGION) awscli -c '\
		GROUP=$$(aws logs describe-log-groups --log-group-name-prefix /aws/lambda/$(FN) --query "logGroups[0].logGroupName" --output text); \
		test -n "$$GROUP" -a "$$GROUP" != "None" || { echo "❌  No log group for /aws/lambda/$(FN)*"; exit 1; }; \
		echo "==> tailing $$GROUP (since $(or $(SINCE),1h))"; \
		aws logs tail "$$GROUP" --since $(or $(SINCE),1h) --format short'

.PHONY: invoke
invoke: ## Invoke a Lambda now and print its tailed execution logs. Usage: make invoke FN=crawler|digest
	@test -n "$(FN)" || { echo "❌  Usage: make invoke FN=crawler|digest"; exit 1; }
	@docker compose run --rm --entrypoint sh -e AWS_DEFAULT_REGION=$(INFRA_REGION) awscli -c '\
		NAME=$$(aws lambda list-functions --query "Functions[].FunctionName" --output text | tr "\t" "\n" | grep "^$(FN)-" | head -1); \
		test -n "$$NAME" || { echo "❌  No function $(FN)-*"; exit 1; }; \
		echo "==> invoking $$NAME (waiting for completion, up to the Lambda timeout) ..."; \
		aws --cli-read-timeout 0 --cli-connect-timeout 60 lambda invoke --function-name "$$NAME" --log-type Tail --query LogResult --output text /tmp/payload >/tmp/b64; \
		echo "--- payload ---"; cat /tmp/payload; echo; echo "--- log tail ---"; base64 -d /tmp/b64'

.PHONY: test
test: ## Run all unit tests (crawler + digest)
	@docker compose run --rm \
		-e DYNAMODB_TABLE=test-table \
		-e AWS_ACCESS_KEY_ID=test \
		-e AWS_SECRET_ACCESS_KEY=test \
		-e AWS_DEFAULT_REGION=eu-west-1 \
		crawler python -m pytest test_handler.py test_sources.py -v
	@docker compose run --rm \
		-e DYNAMODB_TABLE=test-table \
		-e BUCKET_NAME=test-bucket \
		-e AWS_ACCESS_KEY_ID=test \
		-e AWS_SECRET_ACCESS_KEY=test \
		-e AWS_DEFAULT_REGION=eu-west-1 \
		digester python -m pytest test_digest.py -v

.PHONY: test-digest
test-digest: ## Run digest unit tests only (no LocalStack)
	@docker compose run --rm \
		-e DYNAMODB_TABLE=test-table \
		-e BUCKET_NAME=test-bucket \
		-e AWS_ACCESS_KEY_ID=test \
		-e AWS_SECRET_ACCESS_KEY=test \
		-e AWS_DEFAULT_REGION=eu-west-1 \
		digester python -m pytest test_digest.py -v

.PHONY: test-youtube-fetch
test-youtube-fetch: ## Fetch recent videos for each channel and print them (no DDB writes). Needs YOUTUBE_API_KEY.
	@test -n "$(YOUTUBE_API_KEY)" || { echo "❌  YOUTUBE_API_KEY not set in .env"; exit 1; }
	@docker compose run --rm \
		-e YOUTUBE_API_KEY=$(YOUTUBE_API_KEY) \
		-e DYNAMODB_TABLE=test-dummy \
		-e AWS_ACCESS_KEY_ID=test \
		-e AWS_SECRET_ACCESS_KEY=test \
		-e AWS_DEFAULT_REGION=eu-west-1 \
		crawler python scripts/youtube_fetch.py

.PHONY: crawl
crawl: ## Run crawler Lambda locally (RSS feeds + YouTube → DynamoDB; YouTube needs YOUTUBE_API_KEY)
	@test -n "$(DYNAMODB_TABLE)" || { echo "❌  DYNAMODB_TABLE not set in .env"; exit 1; }
	@docker compose run --rm \
		-e DYNAMODB_TABLE=$(DYNAMODB_TABLE) \
		-e YOUTUBE_API_KEY=$(YOUTUBE_API_KEY) \
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
		crawler python scripts/reset_all.py

.PHONY: redigest
redigest: ## Re-run today's digest (unserve today's articles then re-digest)
	$(MAKE) reset-today
	$(MAKE) digest

.PHONY: redigest-prod
redigest-prod: ## Full prod redigest: unserve today + reprocess YouTube + invoke production Lambda
	$(MAKE) reset-today
	$(MAKE) reset-youtube-nosummary
	$(MAKE) invoke FN=digest

.PHONY: reset-today
reset-today: ## Unserve today's articles so digest can be re-run
	@test -n "$(DYNAMODB_TABLE)" || { echo "❌  DYNAMODB_TABLE not set in .env"; exit 1; }
	@docker compose run --rm \
		-e DYNAMODB_TABLE=$(DYNAMODB_TABLE) \
		-e AWS_DEFAULT_REGION=$(INFRA_REGION) \
		crawler python scripts/reset_today.py

.PHONY: reset-youtube-nosummary
reset-youtube-nosummary: ## Clear status+summary for YouTube items with no transcript (prod) so Gemini can reprocess them
	@test -n "$(DYNAMODB_TABLE)" || { echo "❌  DYNAMODB_TABLE not set in .env"; exit 1; }
	@docker compose run --rm \
		-e DYNAMODB_TABLE=$(DYNAMODB_TABLE) \
		-e AWS_DEFAULT_REGION=$(INFRA_REGION) \
		crawler python scripts/reset_youtube_nosummary.py

.PHONY: dev
dev: ## Preview digest HTML locally — uses LocalStack DDB, no S3 upload, opens in browser
	@test -n "$(ANTHROPIC_API_KEY)" || { echo "❌  ANTHROPIC_API_KEY not set in .env"; exit 1; }
	@docker compose ps localstack 2>/dev/null | grep -qE "Up|running" \
		|| { echo "❌  LocalStack not running — run 'make local-up' first"; exit 1; }
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
local-reset: ## Delete all local articles from LocalStack (re-run local-crawl to restart)
	@docker compose ps localstack 2>/dev/null | grep -qE "Up|running" \
		|| { echo "❌  LocalStack not running — run 'make local-up' first"; exit 1; }
	@docker compose run --rm \
		-e DYNAMODB_TABLE=reeds-articles \
		-e AWS_DEFAULT_REGION=eu-west-1 \
		-e AWS_ACCESS_KEY_ID=test \
		-e AWS_SECRET_ACCESS_KEY=test \
		-e AWS_ENDPOINT_URL=http://localstack:4566 \
		crawler python scripts/local_reset.py

.PHONY: local-soft-reset
local-soft-reset: ## Clear AI fields (status/summary) from local articles, keep content (prompt engineering)
	@docker compose ps localstack 2>/dev/null | grep -qE "Up|running" \
		|| { echo "❌  LocalStack not running — run 'make local-up' first"; exit 1; }
	@docker compose run --rm \
		-e DYNAMODB_TABLE=reeds-articles \
		-e AWS_DEFAULT_REGION=eu-west-1 \
		-e AWS_ACCESS_KEY_ID=test \
		-e AWS_SECRET_ACCESS_KEY=test \
		-e AWS_ENDPOINT_URL=http://localstack:4566 \
		crawler python scripts/local_soft_reset.py

.PHONY: local-crawl
local-crawl: ## Fetch RSS feeds (+ YouTube if YOUTUBE_API_KEY set) → LocalStack DynamoDB
	@docker compose ps localstack 2>/dev/null | grep -qE "Up|running" \
		|| { echo "❌  LocalStack not running — run 'make local-up' first"; exit 1; }
	@docker compose run --rm \
		-e DYNAMODB_TABLE=reeds-articles \
		-e YOUTUBE_API_KEY=$(YOUTUBE_API_KEY) \
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
	docker compose run --rm awscli s3 sync /app/public/ s3://$$BUCKET --delete --exclude '.DS_Store' --exclude 'digest/*'; \
	if [ -n "$$CFID" ]; then \
		docker compose run --rm awscli cloudfront create-invalidation --distribution-id $$CFID --paths '/*'; \
	fi

# ── Pulumi — reeds infra (S3 + CloudFront + DynamoDB + Lambda + EventBridge) ──
.PHONY: build-lambdas
build-lambdas: ## Install Lambda pip deps into backend/*/packages/ (auto-run by infra-up/preview)
	docker run --rm --platform linux/amd64 -v "$(CURDIR)":/app python:3.12-slim \
		sh -c "pip install -q -r /app/backend/crawler/requirements.txt  -t /app/backend/crawler/packages/  --upgrade \
		    && pip install -q -r /app/backend/digest/requirements.txt   -t /app/backend/digest/packages/   --upgrade"
	@test -d backend/crawler/packages/yaml                       || { echo "❌  build-lambdas: yaml missing from crawler"; exit 1; }
	@test -d backend/crawler/packages/feedparser                 || { echo "❌  build-lambdas: feedparser missing from crawler"; exit 1; }
	@test -d backend/crawler/packages/googleapiclient            || { echo "❌  build-lambdas: googleapiclient missing from crawler"; exit 1; }
	@test -d backend/crawler/packages/youtube_transcript_api     || { echo "❌  build-lambdas: youtube_transcript_api missing from crawler"; exit 1; }
	@test -d backend/digest/packages/yaml                        || { echo "❌  build-lambdas: yaml missing from digest"; exit 1; }
	@test -d backend/digest/packages/anthropic                   || { echo "❌  build-lambdas: anthropic missing from digest"; exit 1; }
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
