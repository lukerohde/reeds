# reeds — AGENTS.md

Daily blog digest at `reeds.lukeroh.de`. Crawls 10 tech RSS feeds, uses Claude to
filter and summarise, renders a clean HTML digest page to S3/CloudFront daily.

## Architecture

```
EventBridge cron (7pm UTC / 5am AEST)
  → Lambda: crawler   — Extract: RSS feeds + article content → DynamoDB
  → Lambda: digest    — Transform: relevance filter + summarise (AI)
                        Curate: pick top 10 (AI)
                        Load: render HTML → S3
                                               ↓
                                     CloudFront serves it
```

Clean ETL separation: crawler has no AI dependency (cheap, fast, testable).
All AI cost sits in the digest Lambda.

## Key files

| File | Purpose |
|---|---|
| `config/config.yaml` | **All config**: blogs list, AI prompts, digest settings |
| `backend/crawler/handler.py` | Pure extract — RSS + content → DynamoDB |
| `backend/youtube_crawler/handler.py` | Pure extract — YouTube videos → DynamoDB |
| `backend/digest/handler.py` | Transform + curate + render → S3 |
| `backend/digest/template.html` | HTML template with `%HEADING%`, `%ITEMS%`, `%PREV_LINK%` |
| `infra/pulumi/__main__.py` | All AWS infra (S3, CloudFront, DynamoDB, Lambdas, EventBridge) |
| `infra/pulumi/Pulumi.prod.yaml` | Stack config (domain, bucket, DNS mode) |

## Config

Everything tuneable lives in `config/config.yaml`:
- **blogs** — list of `{author, url, feed}` entries
- **settings** — `content_limit`, `candidates_pool`, `max_per_author`, `digest_size`, `words_per_minute`
- **prompts** — `relevance_check`, `summarise`, `curate`

`max_per_author` caps how many articles a single author can contribute to the candidates pool.
Without it, prolific authors (e.g. Simon Willison posts many times daily) dominate the pool and
crowd out other voices even before the curate step runs.

Pulumi bundles `config.yaml` into each Lambda zip via `AssetArchive`.
Docker bind-mounts it into the handler working directories at runtime.
Both handlers load it via `Path(__file__).parent / 'config.yaml'`.

To add a blog: `/add-blog` (discovers feed, verifies, updates config, commits, pushes).

## DNS modes

`infra/pulumi/__main__.py` supports three modes (first match wins):

1. `reeds:parentIngressStack` in Pulumi config — StackReference to an aws-quill ingress stack
2. `reeds:zoneId` in Pulumi config — Route53 zone ID passed directly
3. Neither — reeds creates a new Route53 zone and exports nameservers

## Common commands

```bash
make crawl          # fetch RSS feeds + article content → real DynamoDB
make youtube-crawl  # fetch YouTube videos → real DynamoDB (needs YOUTUBE_API_KEY)
make digest         # transform + curate → HTML → real S3
make redigest       # reset today's articles and re-run digest
make reset-today    # unserve today's articles so digest can be re-run
make reset-all      # ⚠️  delete all articles (use after schema changes)
make test           # run all unit tests (crawler + digest + youtube_crawler)
make test-digest    # run digest unit tests only (no LocalStack)
make test-youtube   # run YouTube crawler unit tests only
make diagnose-author AUTHOR="Simon Willison"  # query DDB stats for an author
make test-feed FEED=<url>  # discover and verify a feed URL
make deploy         # sync public/ assets to S3 + invalidate CloudFront
make build-lambdas  # install pip deps into backend/*/packages/ (auto-run by infra-up)
make infra-up       # deploy/update AWS infrastructure via Pulumi
make infra-outputs  # show bucket, CloudFront ID, etc.

make local-up               # start LocalStack (DynamoDB + S3)
make local-crawl            # crawl → LocalStack DynamoDB (no AI, no AWS)
make local-youtube-crawl    # YouTube crawl → LocalStack (needs YOUTUBE_API_KEY)
make local-reset            # delete all local articles (re-run local-crawl to start fresh)
make local-soft-reset       # clear AI fields only (status/summary) — keep content, re-run AI
make dev                    # digest → preview HTML → open in browser (AI needed, no AWS)
```

## Local development

Uses LocalStack for fully offline development (no AWS required):

```bash
make local-up      # once — start LocalStack, create table + bucket
make local-crawl   # fetch articles into LocalStack
make dev           # AI summarise + curate + render → /tmp/reeds-digest-preview.html
```

Only `ANTHROPIC_API_KEY` is needed. All AWS calls go to `http://localstack:4566`.

Prompt engineering loop:
```bash
make local-crawl        # once — fetch articles into LocalStack
# edit config/config.yaml (prompts section)
make local-soft-reset   # clear AI fields without deleting content
make dev                # re-run AI + preview
```

Note: `local-reset` deletes all articles (you'd need to re-crawl). Use `local-soft-reset`
to iterate on prompts without re-fetching content.

## LocalStack notes

- `local-up` is idempotent — safe to run multiple times (table/bucket creation errors are suppressed).
- LocalStack data persists in Docker volume `reeds_localstack_data` across container restarts.
  To wipe it completely: `docker volume rm reeds_localstack_data` (then re-run `make local-up`).
- `local-crawl`, `local-soft-reset`, `local-reset`, and `dev` all guard against LocalStack not
  running and print a clear error if it isn't.
- `served_date` is stored in Melbourne timezone (by the digest handler). `reset-today` matches
  this — do not change it to UTC or articles served near midnight won't be found.

## DynamoDB schema

Each article item: `url` (PK), `author`, `title`, `published_date`, `fetched_date`,
`served_date`, `word_count`, `content` (≤8000 chars), `status` (relevant/ignored),
`summary`.

`served_date` is empty string until the digest serves the article (DynamoDB can't filter on NULL).

## Lambda packaging

`make build-lambdas` pip-installs each handler's `requirements.txt` into
`backend/<handler>/packages/` (gitignored) using a `python:3.12-slim` container
with `--platform linux/amd64`. The platform flag is required: Lambda runs on
x86_64 Linux, and packages like `pydantic_core` (a dependency of `anthropic`)
have C extensions that must match the target architecture — installing on Apple
Silicon without it produces ARM64 binaries that fail at Lambda startup.

`_lambda_archive()` in `__main__.py` builds the zip in two passes:
1. Flatten everything in `packages/` to the zip root (third-party deps)
2. Add handler source files on top (so `handler.py` etc. always win)
3. Inject `config/config.yaml` at the zip root via the `extra` param

`make infra-up` and `make infra-preview` both depend on `build-lambdas`, so packages
are always current before a deploy — locally or in CI.

Local dev (`make dev`, `make local-crawl`) uses Docker Compose services with
`pip install -r requirements.txt` in their entrypoint — unaffected by this.

## Testing

Three levels, increasing cost:

| Level | Command | What it covers | Needs |
|---|---|---|---|
| Unit | `make test` | Crawler feed parsing | Nothing |
| Integration (no AI) | `make test-integration` | Render + served_date logic | LocalStack |
| Integration (AI) | `make test-integration` | Relevance + summarise + curate | LocalStack + API key |

`make test-integration` auto-skips AI test classes if `ANTHROPIC_API_KEY` is not set.

Package build verification is embedded in `make build-lambdas` — it asserts that
`yaml` and `feedparser`/`anthropic` dirs exist in `backend/*/packages/` after pip install,
so a silent pip failure is caught immediately.

### Claude commands

`/verify-infra` — after a deployment or missed digest: checks EventBridge rules,
Lambda targeting, invoke permissions, and Lambda startup (catches missing packages
before the schedule fires).

`/test-integration` — interactive guide: checks LocalStack is running, runs the
test suite, and interprets failures with suggested fixes.

`/test-all` — full test suite: unit, integration, infra health, and manual checks.

`/teardown` — destroy all reeds infrastructure cleanly (empties S3 first).

`/diagnose-author` — queries DynamoDB for an author's served/unserved breakdown,
position in the candidates pool, and per-day history. Use to debug underrepresentation
or firehose-author problems. Accepts `AUTHOR="Author Name"` argument.

`/check-localstack` — verifies LocalStack is running, the table and bucket exist,
and there are articles to work with. Run before `make dev` or `make test-integration`
if you're getting confusing connection errors.

## YouTube integration

YouTube videos enter the same DynamoDB table as blog articles, with `source: 'youtube'`.
The `youtube_crawler` Lambda fetches recent videos from curated channels via the YouTube Data API v3.
The digest Lambda uses Gemini (not Claude) to summarise YouTube videos directly from their URL.

**Required API keys (beyond `ANTHROPIC_API_KEY`):**
- `YOUTUBE_API_KEY` — YouTube Data API v3 key (for `youtube_crawler` Lambda)
- `GOOGLE_API_KEY` — Gemini API key (for `digest` Lambda, YouTube summarisation)

Add both to `.env` and as GitHub secrets. See `infra/pulumi/__main__.py` for where they're
injected into Lambda env vars.

**Config fields (in `config/config.yaml`):**
- `youtubers` — list of `{name, channel_id}` entries
- `settings.youtube_lookback_days` — how far back to fetch videos per run (default: 7)
- `settings.max_videos_per_channel` — max new videos per channel per crawl (default: 3)
- `prompts.youtube_summarise` — Gemini prompt for video summarisation

**Local dev:**
```bash
# YouTube crawling needs a real YOUTUBE_API_KEY (read-only, free quota)
make local-youtube-crawl   # → LocalStack DynamoDB

# Digest handles YouTube items automatically if GOOGLE_API_KEY is set
make dev                   # picks up YouTube items from LocalStack alongside blog articles
```

**DynamoDB schema additions for YouTube items:**
- `source: 'youtube'` — distinguishes from blog articles
- `video_id` — YouTube video ID (e.g. `dQw4w9WgXcQ`)
- `content: ''` — always empty; Gemini reads from URL at summarisation time

## CI/CD

Push to `main` triggers:
- `deploy-infra.yml` — on changes to `infra/`, `backend/`, `config/`
- `deploy-site.yml` — on changes to `public/`

GitHub secrets required: `PULUMI_ACCESS_TOKEN`, `ANTHROPIC_API_KEY`.
Standalone installs also need `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`.
aws-quill installs use OIDC (role ARN from parent ingress stack output).

## Cost

Target: **< $1/month**

| Resource | Cost |
|---|---|
| Lambda (2 functions, ~60 invocations/month) | Free tier |
| DynamoDB (PAY_PER_REQUEST) | Free tier |
| EventBridge (2 schedules) | Free |
| S3 (~10KB HTML) | ~$0.001/month |
| CloudFront | Free tier |
| ACM certificate | Free |
| Route53 (if standalone zone) | ~$0.50/month |
