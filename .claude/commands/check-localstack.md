# /check-localstack — Verify LocalStack is running and initialised

Check that LocalStack is up, the DynamoDB table and S3 bucket exist, and there are
articles to work with. Run this before `make dev` or `make test-integration` if you're
getting confusing connection errors.

---

## Step 1 — Check if LocalStack container is running

```bash
docker compose ps localstack
```

**Pass:** Shows `Up` or `running` status.

**Fail:** Run `make local-up` — it starts LocalStack and creates the table + bucket.
`make local-up` is idempotent; safe to run even if LocalStack is already running.

---

## Step 2 — Verify DynamoDB table exists

```bash
docker compose run --rm \
  -e AWS_ACCESS_KEY_ID=test \
  -e AWS_SECRET_ACCESS_KEY=test \
  -e AWS_DEFAULT_REGION=eu-west-1 \
  -e AWS_ENDPOINT_URL=http://localstack:4566 \
  awscli dynamodb describe-table --table-name reeds-articles \
  --query 'Table.TableStatus'
```

**Pass:** Returns `"ACTIVE"`.

**Fail:** Table doesn't exist — run `make local-up`.

---

## Step 3 — Verify S3 bucket exists

```bash
docker compose run --rm \
  -e AWS_ACCESS_KEY_ID=test \
  -e AWS_SECRET_ACCESS_KEY=test \
  -e AWS_DEFAULT_REGION=eu-west-1 \
  -e AWS_ENDPOINT_URL=http://localstack:4566 \
  awscli s3 ls s3://reeds-local
```

**Pass:** Command succeeds (bucket exists; may be empty).

**Fail:** Bucket doesn't exist — run `make local-up`.

---

## Step 4 — Check article count in local table

```bash
docker compose run --rm \
  -e AWS_ACCESS_KEY_ID=test \
  -e AWS_SECRET_ACCESS_KEY=test \
  -e AWS_DEFAULT_REGION=eu-west-1 \
  -e AWS_ENDPOINT_URL=http://localstack:4566 \
  awscli dynamodb scan --table-name reeds-articles --select COUNT \
  --query 'Count'
```

**0 items:** Run `make local-crawl` to populate the table (fetches real RSS feeds into LocalStack).

**>0 items:** Ready to run `make dev` or `make test-integration`.

---

## Step 5 — Report

Summarise results as a table:

| Check | Status | Action if failing |
|---|---|---|
| LocalStack container running | ✅/❌ | `make local-up` |
| DynamoDB table `reeds-articles` exists | ✅/❌ | `make local-up` |
| S3 bucket `reeds-local` exists | ✅/❌ | `make local-up` |
| Article count | N articles | 0 → run `make local-crawl` |

If `make local-up` still fails (container networking issue), try:
```bash
docker compose down
docker compose up -d localstack
make local-up
```

If the volume is corrupted, wipe it and start fresh:
```bash
docker compose down -v
make local-up
make local-crawl
```
