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
| `config/config.yaml` | **All config**: blogs, YouTube channels, AI prompts, digest settings |
| `backend/crawler/handler.py` | Pure extract — iterates Sources → DynamoDB (dedup + content-retry loop) |
| `backend/crawler/sources.py` | Pluggable `Source`s: `BlogSource` (RSS) + `YouTubeSource` (videos + transcripts) |
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
- **prompts** — `relevance_check`, `summarise_short`, `summarise_long`, `curate`

`max_per_author` caps how many articles a single author can contribute to the candidates pool.
Without it, prolific authors (e.g. Simon Willison posts many times daily) dominate the pool and
crowd out other voices even before the curate step runs.

Pulumi bundles `config.yaml` into each Lambda zip via `AssetArchive`.
Docker bind-mounts it into the handler working directories at runtime.
Both handlers load it via `Path(__file__).parent / 'config.yaml'`.

To add a blog: `/add-blog` (discovers feed, verifies, updates config, commits, pushes).
To add a YouTube channel: `make add-youtuber HANDLE=@handle` (resolves the channel ID
from the page — no API key needed — and appends it to `config.yaml`).

## DNS modes

`infra/pulumi/__main__.py` supports three modes (first match wins):

1. `reeds:parentIngressStack` in Pulumi config — StackReference to an aws-quill ingress stack
2. `reeds:zoneId` in Pulumi config — Route53 zone ID passed directly
3. Neither — reeds creates a new Route53 zone and exports nameservers

## Common commands

```bash
make crawl          # fetch RSS + YouTube (if YOUTUBE_API_KEY set) → real DynamoDB
make test-youtube-fetch    # print recent videos per channel, no DDB writes (needs YOUTUBE_API_KEY)
make show-candidates       # show relevant unserved articles + summaries (local dev)
make digest         # transform + curate → HTML → real S3
make redigest       # reset today's articles and re-run digest
make reset-today    # unserve today's articles so digest can be re-run
make reset-all      # ⚠️  delete all articles (use after schema changes)
make test           # run all unit tests (crawler + digest)
make test-digest    # run digest unit tests only (no LocalStack)
make add-youtuber HANDLE=@handle   # resolve a YouTube handle/URL → channel ID → add to config
make diagnose-author AUTHOR="Simon Willison"  # query DDB stats for an author
make test-feed FEED=<url>  # discover and verify a feed URL
make deploy         # sync public/ assets to S3 + invalidate CloudFront
make build-lambdas  # install pip deps into backend/*/packages/ (auto-run by infra-up)
make infra-up       # deploy/update AWS infrastructure via Pulumi
make infra-outputs  # show bucket, CloudFront ID, etc.

make local-up               # start LocalStack (DynamoDB + S3)
make local-crawl            # crawl → LocalStack DynamoDB (no AI; YouTube if YOUTUBE_API_KEY set)
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

Package build verification is embedded in `make build-lambdas` — it asserts that the
expected dirs exist in `backend/*/packages/` after pip install (`feedparser`,
`googleapiclient`, `youtube_transcript_api` for the crawler; `anthropic` for the digest),
so a silent pip failure is caught immediately.

### Slash commands

Claude Code slash commands live in `.claude/commands/`. See `CLAUDE.md` for the
full index (`/setup`, `/add-blog`, `/check-localstack`, `/test-integration`,
`/test-all`, `/verify-infra`, `/diagnose-author`, `/teardown`).

## Sources (pluggable extraction)

The crawler is source-agnostic. A `Source` (`backend/crawler/sources.py`) implements just
two methods:

- `discover()` → list of item dicts (`url`, `author`, `title`, `published_date`, …)
- `fetch_content(item)` → the item's full text (`''` if unavailable)

Everything else — dedup by `url`, the item schema, `content_limit` truncation, `word_count`,
and the store/retry loop in `handler.crawl()` — is identical for every source. Adding a
source is one small class plus an entry in `build_sources()`.

Two sources ship today:
- **`BlogSource`** — RSS via `feedparser`; `fetch_content` cleans article HTML with BeautifulSoup.
- **`YouTubeSource`** — recent uploads via the YouTube Data API v3; `fetch_content` returns the
  video transcript via `youtube_transcript_api`. Opt-in: only added by `build_sources()` when
  channels are configured **and** `YOUTUBE_API_KEY` is set.

YouTube items land in the same table as blogs, distinguished only by `source: 'youtube'`
(plus a `video_id`). Their transcript is stored in `content` — exactly like a blog body —
so the digest transforms them with the **same** `is_relevant()` + `make_summary()` path,
with no source-specific branching. A video with no captions is stored with `content: ''`;
the crawler retries its transcript on later runs (clearing `status`/`summary` so the digest
reprocesses it on success). Until a transcript appears it's relevance-checked on the title
alone and served without a summary — exactly how a content-less blog item behaves.

**Required API key (beyond `ANTHROPIC_API_KEY`):**
- `YOUTUBE_API_KEY` — YouTube Data API v3 key (read-only, free quota), injected into the
  crawler Lambda. Without it, the YouTube source is simply skipped.

**Config fields (in `config/config.yaml`):**
- `youtubers` — list of `{name, channel_id}` entries (verify IDs with `make test-youtube-fetch`)
- `settings.youtube_lookback_days` — how far back to fetch videos per run (default: 7)
- `settings.max_videos_per_channel` — max new videos per channel per crawl (default: 3)
- `settings.summarise_long_threshold` — word count at/above which the TLDR prompt is used (default: 500)
- `prompts.summarise_short` / `prompts.summarise_long` — short/long content prompts (blogs and videos alike)

**Local dev:**
```bash
make add-youtuber HANDLE=@handle   # resolve channel ID from a handle/URL → config (no API key)
make test-youtube-fetch    # print what videos exist for each channel (no DDB writes)
make local-crawl           # RSS + YouTube (if YOUTUBE_API_KEY set) → LocalStack
make dev                   # ANTHROPIC_API_KEY required
```

**DynamoDB schema additions for YouTube items:**
- `source: 'youtube'` — distinguishes from blog articles (blogs get `source: 'blog'`)
- `video_id` — YouTube video ID (e.g. `dQw4w9WgXcQ`)
- `content` — the transcript text (or `''` if captions were unavailable; retried next crawl)

## CI/CD

Push to `main` triggers:
- `deploy-infra.yml` — on changes to `infra/`, `backend/`, `config/`
- `deploy-site.yml` — on changes to `public/`

GitHub secrets required: `PULUMI_ACCESS_TOKEN`, `ANTHROPIC_API_KEY` (plus
`YOUTUBE_API_KEY` if YouTube is enabled).
Standalone installs also need `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`.
aws-quill installs use OIDC (role ARN from parent ingress stack output).

## Cost

Target **< $1/month** — full breakdown in [`ARCHITECTURE.md`](ARCHITECTURE.md).
Route53 adds ~$0.50/month only when reeds creates a standalone zone (see DNS modes above).
