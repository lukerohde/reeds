# /verify-infra — Smoke test deployed reeds infrastructure

Check that the deployed AWS resources are correctly configured and both Lambdas
can actually start. Run this after any deployment or when the nightly digest
doesn't appear. Reports a clear pass/fail for each check with specific remediation.

**Important:** You need AWS credentials to run these checks. Use the `awscli` Docker
service so credentials come from `.env`, same as `make crawl` / `make digest`.

---

## Step 1 — Get deployed resource names

```bash
docker compose run --rm pulumi stack output
```

Note `dynamodb_table`, `reeds_bucket`, `reeds_distribution_id`.

Then get Lambda function names:
```bash
docker compose run --rm -e AWS_DEFAULT_REGION=$(grep -m1 'aws:region' infra/pulumi/Pulumi.prod.yaml | awk '{print $2}') \
  awscli lambda list-functions --query 'Functions[].FunctionName' --output text
```

Store both function names. They look like `crawler-<hash>` and `digest-<hash>`.

---

## Step 2 — Check EventBridge rules

```bash
docker compose run --rm -e AWS_DEFAULT_REGION=<REGION> \
  awscli events list-rules --query 'Rules[].{Name:Name,State:State,Schedule:ScheduleExpression}' --output table
```

**Pass:** Both rules exist, both show `State: ENABLED`, schedules are
`cron(0 19 * * ? *)` and `cron(10 19 * * ? *)`.

**Fail:** If State is `DISABLED`, re-enable:
```bash
docker compose run --rm -e AWS_DEFAULT_REGION=<REGION> \
  awscli events enable-rule --name <rule-name>
```

---

## Step 3 — Check EventBridge → Lambda targeting

For each rule, verify its target ARN matches the deployed Lambda ARN:

```bash
docker compose run --rm -e AWS_DEFAULT_REGION=<REGION> \
  awscli events list-targets-by-rule --rule <rule-name>

docker compose run --rm -e AWS_DEFAULT_REGION=<REGION> \
  awscli lambda get-function --function-name <function-name> \
  --query 'Configuration.FunctionArn'
```

**Pass:** The ARN in the EventBridge target matches the Lambda function ARN.

**Fail:** ARN mismatch means Pulumi state is inconsistent. Run `make infra-up` to reconcile.

---

## Step 4 — Check Lambda invoke permissions

```bash
docker compose run --rm -e AWS_DEFAULT_REGION=<REGION> \
  awscli lambda get-policy --function-name <function-name>
```

**Pass:** Policy contains a statement where `Principal.Service = events.amazonaws.com`
and `Condition.ArnLike.AWS:SourceArn` matches the EventBridge rule ARN.

**Fail:** Missing permission means EventBridge can't invoke the Lambda.
Run `make infra-up` to restore it.

---

## Step 5 — Invoke each Lambda and check for import errors

This is the most important check — it catches missing packages before the nightly
schedule fires.

```bash
docker compose run --rm -e AWS_DEFAULT_REGION=<REGION> \
  awscli lambda invoke \
  --function-name <function-name> \
  --payload '{}' \
  --log-type Tail \
  /tmp/lambda-test.json
```

Decode the `LogResult` field (it's base64):
```bash
echo "<LogResult value>" | base64 -d
```

**Pass:** No `Runtime.ImportModuleError` in the decoded logs. The function may
return an error (e.g., missing env vars in a test invocation) but it started.

**Fail — `No module named 'yaml'` or similar:** Packages are missing from the zip.
Run `make infra-up` (which runs `make build-lambdas` first) to redeploy.

**Fail — any other error:** Read the full decoded log and reason about the cause.

---

## Step 6 — Verify recent digest in S3

```bash
docker compose run --rm awscli s3 ls s3://<reeds_bucket>/digest/ --recursive \
  | sort | tail -5
```

**Pass:** You see a `digest/<today>/index.html` object with a recent timestamp.

**Fail:** If the most recent digest is more than 25 hours old, the nightly run
missed. Check the Lambda invocation logs via Step 5 and look at DynamoDB unserved
count:
```bash
docker compose run --rm -e AWS_DEFAULT_REGION=<REGION> \
  awscli dynamodb scan --table-name reeds-articles \
  --filter-expression "served_date = :d" \
  --expression-attribute-values '{":d": {"S": ""}}' \
  --select COUNT
```
A large unserved count with no recent digest means the Lambda ran but failed,
or didn't run at all.

---

## Summary

Report results as a table:

| Check | Status | Notes |
|---|---|---|
| EventBridge rules enabled | ✅/❌ | |
| EventBridge targets correct Lambda ARNs | ✅/❌ | |
| Lambda invoke permissions | ✅/❌ | |
| Crawler Lambda starts cleanly | ✅/❌ | |
| Digest Lambda starts cleanly | ✅/❌ | |
| Recent digest in S3 | ✅/❌ | age of most recent |

If anything failed, provide the specific `make` command or manual fix.
