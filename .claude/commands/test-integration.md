# /test-integration — Run reeds integration tests

Run the digest integration test suite against LocalStack. Tests progress through
three levels: render-only (free), curate with AI (one API call), and full pipeline.

---

## Step 1 — Check prerequisites

```bash
docker compose ps localstack 2>/dev/null | grep -qE "Up|running" \
  && echo "✅  LocalStack running" || echo "❌  LocalStack not running"
```

If LocalStack isn't running:
```bash
make local-up
```

Check for ANTHROPIC_API_KEY (needed for AI test classes):
```bash
grep -qE '^ANTHROPIC_API_KEY=.+' .env \
  && echo "✅  ANTHROPIC_API_KEY set — all test classes will run" \
  || echo "ℹ️   No ANTHROPIC_API_KEY — only TestRenderOnly will run"
```

Tell the user which test classes will run before proceeding.

---

## Step 2 — Run the tests

```bash
make test-integration
```

This runs `pytest backend/digest/test_integration.py -v` inside the digester
Docker container against LocalStack. The `make` target handles table/bucket
creation automatically.

---

## Step 3 — Interpret results

**All green:** The digest pipeline is working correctly. Note which test classes ran.

**TestRenderOnly failures** — these are pure logic bugs, no AWS or AI involved:
- `test_produces_html_file` failing → handler isn't writing to PREVIEW path
- `test_dry_run_does_not_set_served_date` failing → DRY_RUN flag not being respected
- `test_already_served_articles_excluded` failing → DynamoDB filter is broken
- `test_no_articles_returns_early` failing → empty-table guard is missing

**TestCurateWithAI failures** — AI or parsing issues:
- Curate returning 0 articles → the AI response parser may not be extracting URLs
  correctly (check the `selected_urls` logic in `curate()`)
- Served count > 10 → curate fallback is too broad

**TestFullPipeline failures:**
- `test_off_topic_article_ignored` failing → relevance prompt is too permissive;
  consider tightening `prompts.relevance_check` in `config/config.yaml`
- `test_status_written_to_dynamodb` failing → DynamoDB update in `transform()` is broken
- Low served count on `test_transform_produces_output` → relevance filter too strict

For any failure, read the pytest output carefully — it includes the actual vs
expected values. If uncertain, re-run just the failing test:
```bash
docker compose run --rm \
  -v /tmp:/tmp \
  -e DYNAMODB_TABLE=reeds-articles \
  -e BUCKET_NAME=reeds-local \
  -e ANTHROPIC_API_KEY=$(grep -m1 '^ANTHROPIC_API_KEY=' .env | cut -d= -f2-) \
  -e AWS_DEFAULT_REGION=eu-west-1 \
  -e AWS_ACCESS_KEY_ID=test \
  -e AWS_SECRET_ACCESS_KEY=test \
  -e AWS_ENDPOINT_URL=http://localstack:4566 \
  -e DIGEST_DRY_RUN=1 \
  digester \
  python -m pytest test_integration.py::TestRenderOnly::test_html_structure -v -s
```

---

## Step 4 — (Optional) Inspect the preview

If the tests passed and you want to see what the digest looks like:
```bash
open /tmp/reeds-digest-preview.html
```

---

## Step 5 — Report

Summarise:
- Which test classes ran (based on whether ANTHROPIC_API_KEY was set)
- How many tests passed / failed
- For any failures: the test name, what was asserted, and what actually happened
- Suggested fix if the cause is identifiable
