# reeds — AGENTS.md

Agent operational guide for reeds, a daily blog + YouTube digest at `reeds.lukeroh.de`.

See [`README.md`](README.md) for the project overview, setup, and quickstart, and
[`ARCHITECTURE.md`](ARCHITECTURE.md) for the full system diagram and cost breakdown.
This file covers the operational detail needed to work in the codebase.

Clean ETL separation: the crawlers have no AI dependency (cheap, fast, testable);
all AI cost sits in the digest Lambda.

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
- **youtubers** — list of `{name, channel_id}` entries
- **settings** — `content_limit`, `candidates_pool`, `max_per_author`, `digest_size`,
  `words_per_minute`, `summarise_long_threshold`, `youtube_lookback_days`, `max_videos_per_channel`
- **prompts** — `relevance_check`, `summarise_short`, `summarise_long`, `youtube_summarise`, `curate`

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
make youtube-crawl  # fetch YouTube videos + transcripts → real DynamoDB (needs YOUTUBE_API_KEY)
make test-youtube-fetch    # print recent videos per channel, no DDB writes (needs YOUTUBE_API_KEY)
make show-candidates       # show relevant unserved articles + summaries (local dev)
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

### Slash commands

Claude Code slash commands live in `.claude/commands/`. See `CLAUDE.md` for the
full index (`/setup`, `/add-blog`, `/check-localstack`, `/test-integration`,
`/test-all`, `/verify-infra`, `/diagnose-author`, `/teardown`).

## YouTube integration

YouTube videos enter the same DynamoDB table as blog articles, with `source: 'youtube'`.
The `youtube_crawler` Lambda fetches recent videos from curated channels via the YouTube
Data API v3 **and** extracts each video's transcript (`youtube_transcript_api`), storing it
in `content` — exactly like a blog article's body.

The digest Lambda then treats YouTube items two ways:
1. **Transcript present** — same path as blogs: `is_relevant()` then `make_summary()` (Claude),
   with word-count-based short/long prompt selection.
2. **No transcript** (captions disabled / fetch failed) — falls back to `gemini_summarise_video(url)`,
   which has Gemini summarise the video directly from its URL. That output doubles as both the
   relevance signal and the stored summary (no second Claude call).

The crawler retries the transcript on the next run for any unserved video stored without one,
clearing its `status`/`summary` so the digest reprocesses it.

**Required API keys (beyond `ANTHROPIC_API_KEY`):**
- `YOUTUBE_API_KEY` — YouTube Data API v3 key (for `youtube_crawler` Lambda)
- `GOOGLE_API_KEY` — Gemini API key (digest Lambda, transcript-less fallback only; optional)

Add them to `.env` and as GitHub secrets. See `infra/pulumi/__main__.py` for where they're
injected into Lambda env vars. Without `GOOGLE_API_KEY`, a transcript-less video simply gets
an empty summary; videos with transcripts are unaffected.

**Config fields (in `config/config.yaml`):**
- `youtubers` — list of `{name, channel_id}` entries (verify IDs with `make test-youtube-fetch`)
- `settings.youtube_lookback_days` — how far back to fetch videos per run (default: 7)
- `settings.max_videos_per_channel` — max new videos per channel per crawl (default: 3)
- `settings.summarise_long_threshold` — word count at/above which the TLDR prompt is used (default: 500)
- `prompts.youtube_summarise` — Gemini prompt for the transcript-less fallback
- `prompts.summarise_short` / `prompts.summarise_long` — Claude prompts for short/long content

**Local dev:**
```bash
# YouTube crawling needs a real YOUTUBE_API_KEY (read-only, free quota)
make local-youtube-crawl   # → LocalStack DynamoDB (fetches videos + transcripts)
make test-youtube-fetch    # print what videos exist for each channel (no DDB writes)

# Digest handles YouTube items automatically; GOOGLE_API_KEY only needed for the
# transcript-less fallback
make dev                   # ANTHROPIC_API_KEY required; GOOGLE_API_KEY optional
```

**DynamoDB schema additions for YouTube items:**
- `source: 'youtube'` — distinguishes from blog articles
- `video_id` — YouTube video ID (e.g. `dQw4w9WgXcQ`)
- `content` — the transcript text (or `''` if none was available; Gemini fallback reads the URL)

## CI/CD

Push to `main` triggers:
- `deploy-infra.yml` — on changes to `infra/`, `backend/`, `config/`
- `deploy-site.yml` — on changes to `public/`

GitHub secrets required: `PULUMI_ACCESS_TOKEN`, `ANTHROPIC_API_KEY` (plus
`GOOGLE_API_KEY` and `YOUTUBE_API_KEY` if YouTube is enabled).
Standalone installs also need `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`.
aws-quill installs use OIDC (role ARN from parent ingress stack output).

## Cost

Target **< $1/month** — full breakdown in [`ARCHITECTURE.md`](ARCHITECTURE.md).
Route53 adds ~$0.50/month only when reeds creates a standalone zone (see DNS modes above).
