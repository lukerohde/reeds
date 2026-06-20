# /test-all — Full test suite for reeds

Run every test — automated, AI-assisted, and manual checks that can't yet be
automated. Produces a single report at the end with pass/fail for each item.

Work through the sections in order. Don't skip a section because a previous one
failed — continue and report everything. Some later checks are independent.

---

## Section 1 — Unit tests

```bash
make test
```

**Pass:** All tests green.
**Fail:** A feed-parsing regression. Fix before proceeding — unit tests are the
foundation everything else builds on.

---

## Section 2 — Lambda package build + verification

```bash
make build-lambdas
```

This installs pip deps into `backend/*/packages/` and immediately asserts the
expected dirs are present (`feedparser`, `googleapiclient`, `youtube_transcript_api`
for the crawler; `anthropic` for the digest). The build failing here is what caused
the Lambda outage — catching it here means catching it before deploy.

**Pass:** Ends with `✅  Lambda packages verified`.
**Fail:** pip install failed or a required package dir is missing. Check the
pip output for errors.

---

## Section 3 — LocalStack integration tests

First check LocalStack is running:
```bash
docker compose ps localstack 2>/dev/null | grep -qE "Up|running" \
  && echo "✅  LocalStack running" \
  || echo "❌  not running — run 'make local-up' then re-run /test-all"
```

If not running, note it in the report and skip to Section 4.

If running:
```bash
make test-integration
```

Note which test classes ran (TestRenderOnly is always free; AI classes run only
if `ANTHROPIC_API_KEY` is set).

**Pass:** All tests green.
**Fail:** Note the failing test name and error. Continue to Section 4.

---

## Section 4 — Deployed infrastructure health

Get the AWS region:
```bash
grep -m1 'aws:region' infra/pulumi/Pulumi.prod.yaml | awk '{print $2}'
```

### 4a — EventBridge rules

```bash
docker compose run --rm -e AWS_DEFAULT_REGION=<REGION> \
  awscli events list-rules \
  --query 'Rules[].{Name:Name,State:State,Schedule:ScheduleExpression}' \
  --output table
```

**Pass:** Both rules ENABLED, schedules are `cron(0 19 * * ? *)` and `cron(10 19 * * ? *)`.

### 4b — Lambda functions exist and are Active

```bash
docker compose run --rm -e AWS_DEFAULT_REGION=<REGION> \
  awscli lambda list-functions \
  --query 'Functions[?contains(FunctionName,`crawler`) || contains(FunctionName,`digest`)].{Name:FunctionName,Modified:LastModified}' \
  --output table
```

Note the function names. Then verify each is Active (list-functions doesn't
reliably return State in all API versions):
```bash
docker compose run --rm -e AWS_DEFAULT_REGION=<REGION> \
  awscli lambda get-function-configuration \
  --function-name <function-name> \
  --query '{State:State,LastUpdateStatus:LastUpdateStatus}'
```

**Pass:** Both functions exist, `State: Active`, `LastUpdateStatus: Successful`.

### 4c — EventBridge targets match deployed Lambda ARNs

For each rule, confirm the target ARN matches the deployed function ARN:
```bash
docker compose run --rm -e AWS_DEFAULT_REGION=<REGION> \
  awscli events list-targets-by-rule --rule <rule-name> \
  --query 'Targets[].Arn' --output text
```

**Pass:** Target ARN ends with the same function name you saw in 4b.

### 4d — EventBridge has permission to invoke each Lambda

Checks the Lambda resource policy — this is what authorises EventBridge to invoke
the function. Without it, EventBridge silently drops the scheduled invocation.

```bash
docker compose run --rm -e AWS_DEFAULT_REGION=<REGION> \
  awscli lambda get-policy --function-name <function-name> \
  --query 'Policy' --output text
```

Parse the returned JSON and verify:
- `Principal.Service` = `events.amazonaws.com`
- `Condition.ArnLike["AWS:SourceArn"]` matches the EventBridge rule ARN from 4a

**Pass:** Both functions have this statement.
**Fail:** Missing policy means EventBridge cannot invoke the Lambda despite the
rule being enabled. Run `make infra-up` to restore the permission.

### 4e — Lambda actually runs end-to-end (real prod execution)

**Note: this invokes the real Lambda against prod DynamoDB / S3.** The crawler
will fetch live RSS feeds and write articles. That's intentional — it's what
the nightly schedule does.

Invoke the crawler synchronously and wait for it to complete (~30–60s):
```bash
docker compose run --rm -e AWS_DEFAULT_REGION=<REGION> \
  awscli lambda invoke \
  --function-name <crawler-function-name> \
  --payload '{}' \
  --log-type Tail \
  /tmp/crawler-result.json
```

Decode the log and check the response:
```bash
echo "<LogResult>" | base64 -d
cat /tmp/crawler-result.json
```

**Pass:** `StatusCode: 200`, no `FunctionError` field in the CLI output, response
body contains `{"fetched": <n>, "skipped": <n>}` (or similar). No
`Runtime.ImportModuleError` in decoded log.

**Fail — `No module named 'yaml'` or similar:** Packages missing — run `make infra-up`.
(Locally, Pulumi will prompt for confirmation — run `make infra-up PULUMI_YES=--yes`
or answer `yes` at the prompt. In CI the flag is set automatically.)
**Fail — `FunctionError: Unhandled`:** Lambda started but the handler threw. Read
the decoded log for the traceback.

Then invoke the digest synchronously (~2–5 min due to AI calls):
```bash
docker compose run --rm -e AWS_DEFAULT_REGION=<REGION> \
  awscli lambda invoke \
  --function-name <digest-function-name> \
  --payload '{}' \
  --log-type Tail \
  /tmp/digest-result.json
```

```bash
echo "<LogResult>" | base64 -d
cat /tmp/digest-result.json
```

**Pass:** `StatusCode: 200`, no `FunctionError`, response body contains
`{"served": <n>, "date": "<today>", "urls": [...]}`.
**Fail — `served: 0`:** No unserved articles in DynamoDB. Run `make crawl` first,
then retry the digest invocation.
**Fail — `FunctionError`:** Read the decoded log for the traceback.

### 4f — Recent digest in S3

```bash
docker compose run --rm awscli s3 ls s3://<reeds_bucket>/digest/ \
  | sort | tail -5
```

**Pass:** Most recent `digest/<date>/index.html` is ≤ 25 hours old.
**Fail:** Calculate how many days the digest has been missing. If Sections 4a–4d
passed, a stale digest means the Lambda ran but found no unserved articles (check
DynamoDB unserved count below) or the Lambda had a runtime error that only shows
in CloudWatch (which this IAM user can't read).

Check unserved article count:
```bash
docker compose run --rm -e AWS_DEFAULT_REGION=<REGION> \
  awscli dynamodb scan --table-name reeds-articles \
  --filter-expression "served_date = :d" \
  --expression-attribute-values '{":d": {"S": ""}}' \
  --select COUNT
```

---

## Section 5 — Manual / not-yet-automated checks

These require human eyes or aren't worth automating at current scale. Work through
each one and note pass/fail in the final report.

**5a — Live site loads**
Open `https://reeds.lukeroh.de` in a browser (or `curl -I https://reeds.lukeroh.de`).
- Does it return 200?
- Does it redirect to `/digest/latest/` or show the digest directly?

**5b — Latest digest renders correctly**
Open `https://reeds.lukeroh.de/digest/latest/` in a browser.
- Are there articles?
- Do the article links point to real URLs (not `example.com`)?
- Does the date in the heading match today (or yesterday if before 5am AEST)?
- Does the "← previous" link work?

**5c — RSS feeds still reachable**
Spot-check two or three feeds from `config/config.yaml`:
```bash
curl -s --max-time 10 -o /dev/null -w "%{http_code}" <feed-url>
```
**Pass:** 200 or 301/302.
**Fail:** A feed returning 404 or timing out means that blog may have moved.
Run `/add-blog` to find the new feed URL and update config.

**5d — Anthropic API key still valid**
If Section 3 ran AI test classes successfully, this is already confirmed.
If not, check:
```bash
grep -qE '^ANTHROPIC_API_KEY=.+' .env && echo "key is set" || echo "key is missing"
```
Then run `make dev` (which uses the real API) to confirm it actually works.

---

## Final report

Produce a summary table:

| # | Check | Status | Notes |
|---|---|---|---|
| 1 | Unit tests (`make test`) | ✅/❌ | |
| 2 | Lambda package build (`make build-lambdas`) | ✅/❌ | |
| 3 | Integration: TestRenderOnly | ✅/❌/⏭️ skipped |
| 3 | Integration: TestCurateWithAI | ✅/❌/⏭️ no API key |
| 3 | Integration: TestFullPipeline | ✅/❌/⏭️ no API key |
| 4a | EventBridge rules enabled | ✅/❌ | |
| 4b | Lambda functions Active | ✅/❌ | |
| 4c | EventBridge → Lambda ARN match | ✅/❌ | |
| 4d | EventBridge resource policy on each Lambda | ✅/❌ | |
| 4e | Crawler Lambda runs end-to-end in prod | ✅/❌ | articles fetched |
| 4e | Digest Lambda runs end-to-end in prod | ✅/❌ | articles served |
| 4f | Recent digest in S3 | ✅/❌ | age of most recent |
| 5a | Live site loads | ✅/❌ | |
| 5b | Latest digest renders correctly | ✅/❌ | |
| 5c | RSS feeds reachable | ✅/❌ | note any broken feeds |
| 5d | Anthropic API key valid | ✅/❌/⏭️ covered by §3 |

If anything failed, list specific next steps.
