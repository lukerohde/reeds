# reeds

Daily digest of AI, devops, and software engineering writing at [reeds.lukeroh.de](https://reeds.lukeroh.de).

Built to replace a scattered news feed with a single curated page of the day's best writing from select blogs and YouTube channels. Also a testbed for a cost-effective serverless recipe: Lambda + EventBridge + DynamoDB, with LocalStack for fully offline local development.

## Architecture

Clean ETL pipeline — an extract Lambda feeds a transform/load Lambda:

```
EventBridge (daily cron)
  → Lambda: crawler   — Extract: RSS feeds + YouTube videos/transcripts → DynamoDB
  → Lambda: digest    — Transform: relevance filter → summarise → curate top 10
                        Load: render HTML → S3
                                               ↓
                                     CloudFront serves it
```

The crawler is source-agnostic: blogs and YouTube channels are pluggable `Source`
modules that feed the same pipeline (see [`AGENTS.md`](AGENTS.md)).

Runs entirely on free-tier-friendly serverless: Lambda + EventBridge + DynamoDB + S3 + CloudFront.
**Target: < $1/month** — see [`ARCHITECTURE.md`](ARCHITECTURE.md) for the full system diagram and cost breakdown.

## Sources tracked

Blogs and YouTube channels — alongside prompts and digest settings — live in
[`config/config.yaml`](config/config.yaml) (the `blogs` and `youtubers` lists).

To add a new blog, open this repo in [Claude Code](https://claude.ai/code) and run `/add-blog` —
Claude will discover the feed URL, verify it, add it to config, and push.

To add a YouTube channel: `make add-youtuber HANDLE=@handle` — it resolves the channel ID
straight from the page (no API key needed) and appends it to `config/config.yaml`.

## Commands

Run `make help` for the full list. The common ones:

```bash
make crawl          # fetch RSS feeds + YouTube videos/transcripts → real DynamoDB
make digest         # transform + curate → HTML → real S3
make redigest       # reset today's articles and re-run digest (local Lambda code)
make redigest-prod  # reset today + reprocess YouTube + invoke production Lambda
make deploy         # sync public/ static assets to S3
make infra-up       # deploy/update AWS infrastructure via Pulumi
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
is needed — LocalStack handles AWS locally with dummy credentials. (YouTube is crawled
automatically when `YOUTUBE_API_KEY` is set.)

Prompt engineering workflow:
```bash
make local-crawl        # once — fetch articles into LocalStack
make local-soft-reset   # clear AI fields (status/summary) without deleting content
# edit config/config.yaml (prompts section)
make dev                # re-run transform + curate + preview
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
# Optional — YouTube integration:
# YOUTUBE_API_KEY=
# Optional — Gemini fallback for YouTube videos without transcripts:
# GOOGLE_API_KEY=
```

## CI/CD

GitHub secrets: `PULUMI_ACCESS_TOKEN` (+ `ANTHROPIC_API_KEY` for infra deploy; plus
`YOUTUBE_API_KEY` if YouTube is enabled; plus `GOOGLE_API_KEY` for Gemini fallback).
AWS credentials via OIDC from the parent ingress stack — no AWS keys stored in GitHub.

Push to `main` triggers:
- `deploy-infra.yml` — on changes to `infra/pulumi/`, `backend/`, `config/`
- `deploy-site.yml` — on changes to `public/`

`deploy-infra.yml` runs `make infra-up`, which automatically runs `make build-lambdas`
first to pip-install handler dependencies into the Lambda zips.
