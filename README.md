# reeds

Daily digest of AI, devops, and software engineering writing at [reeds.lukeroh.de](https://reeds.lukeroh.de).

Built to replace a scattered news feed with a single curated page of the day's best writing from select feeds. Also a testbed for a cost-effective serverless recipe: Lambda + EventBridge + DynamoDB, with LocalStack for fully offline local development.

## Architecture

Clean ETL pipeline:

```
EventBridge (daily cron)
  → Lambda: crawler   — Extract: RSS feeds + article content → DynamoDB
  → Lambda: digest    — Transform: relevance filter → summarise → curate top 10
                        Load: render HTML → S3
                                               ↓
                                     CloudFront serves it
```

| Resource | Cost |
|---|---|
| Lambda (2 functions, ~60 invocations/month) | Free tier |
| DynamoDB (PAY_PER_REQUEST) | Free tier |
| EventBridge (2 schedules) | Free |
| S3 (~10 KB HTML) | ~$0.001/month |
| CloudFront | Free tier |
| ACM certificate | Free |

**Target: < $1/month.**

## Blogs tracked

Simon Willison · Andrej Karpathy · Martin Fowler · Charity Majors · Thorsten Ball ·
Kent Beck · Henrik Kniberg · Steve Yegge · Addy Osmani · Bryan Cantrill

Configured in [`config/config.yaml`](config/config.yaml) alongside prompts and digest settings.

To add a new blog, open this repo in [Claude Code](https://claude.ai/code) and run:
```
/add-blog
```
Claude will discover the feed URL, verify it, add it to config, and push.

## Commands

```bash
make crawl          # fetch RSS feeds + article content → real DynamoDB
make digest         # transform + curate → HTML → real S3
make redigest       # reset today's articles and re-run digest
make reset-today    # unserve today's articles so digest can be re-run
make reset-all      # ⚠️  delete all articles (use after schema changes)
make test-feed FEED=<url>  # test whether a feed URL is parseable
make deploy         # sync public/ static assets to S3
make infra-up       # deploy/update AWS infrastructure via Pulumi
make infra-outputs  # show bucket name, CloudFront ID, etc.
```

## Local development (no AWS required)

Uses [LocalStack](https://localstack.cloud) to run DynamoDB and S3 locally in Docker.

```bash
make local-up      # start LocalStack, create DynamoDB table + S3 bucket (first time)
make local-crawl   # fetch RSS feeds + article content → local DynamoDB (no AI)
make dev           # transform (AI) + curate → preview HTML → open in browser
make local-reset   # delete all local articles (re-run local-crawl to start fresh)
```

`make dev` writes to `/tmp/reeds-digest-preview.html` and opens it. Only `ANTHROPIC_API_KEY`
is needed — LocalStack handles AWS locally with dummy credentials.

Prompt engineering workflow:
```bash
make local-crawl   # once — fetch articles into LocalStack
make local-reset   # wipe AI results
# edit config/config.yaml (prompts section)
make dev           # re-run transform + curate + preview
```

## Setup

Requires Docker, `gh` CLI, AWS credentials, Pulumi Cloud account, and Anthropic API key.

Open this repo in [Claude Code](https://claude.ai/code) and run:

```
/setup
```

Claude will walk you through installation and customisation — domain, parent ingress stack, API keys, first crawl, and first digest.

Copy `.env.example` to `.env` and fill in credentials:

```
PULUMI_ACCESS_TOKEN=
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
AWS_DEFAULT_REGION=eu-west-1
ANTHROPIC_API_KEY=
DYNAMODB_TABLE=reeds-articles
```

## CI/CD

One GitHub secret needed: `PULUMI_ACCESS_TOKEN` (+ `ANTHROPIC_API_KEY` for infra deploy).
AWS credentials via OIDC from the parent ingress stack — no AWS keys stored in GitHub.

Push to `main` triggers:
- `deploy-infra.yml` — on changes to `infra/pulumi/`, `backend/`, `config/`
- `deploy-site.yml` — on changes to `public/`

`deploy-infra.yml` runs `make infra-up`, which automatically runs `make build-lambdas`
first to pip-install handler dependencies into the Lambda zips.
