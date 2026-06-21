# /lambda-logs — Read Lambda execution logs first-hand

See what the deployed `crawler` or `digest` Lambda actually did, instead of inferring
from DynamoDB. Two paths depending on whether the deploy user has CloudWatch Logs read.

**Requires:** AWS credentials in `.env` (the `lukerohde-pulumi-user` deploy keys)

---

## Option A — Invoke now + inline log tail (no extra IAM needed)

Runs the function synchronously and prints the last ~4 KB of its execution log. Needs only
`lambda:InvokeFunction`, which the deploy user already has. The crawler is idempotent (it
dedups), so re-running it is safe; the digest will re-render and re-serve.

```bash
make invoke FN=crawler     # fetch RSS + YouTube, print logs
make invoke FN=digest      # relevance + summarise + curate + render, print logs
```

This is also the way to **manually trigger** a run (equivalent to the EventBridge schedule firing).

---

## Option B — Tail historical CloudWatch logs (needs the IAM grant below)

```bash
make logs FN=crawler            # last 1h
make logs FN=digest SINCE=3d    # last 3 days
make logs FN=crawler SINCE=30m
```

`make logs` resolves the hashed log group (`/aws/lambda/crawler-<hash>`) automatically and
runs `aws logs tail` in the dockerised AWS CLI, using the deploy region from `Pulumi.prod.yaml`.

### One-time IAM grant

By default `make logs` fails with `AccessDeniedException ... logs:DescribeLogGroups` — the
deploy user has no CloudWatch Logs read. Grant it once (needs IAM-admin creds, not the
deploy user's own keys):

```bash
aws iam put-user-policy \
  --user-name lukerohde-pulumi-user \
  --policy-name reeds-logs-read \
  --policy-document '{
    "Version": "2012-10-17",
    "Statement": [{
      "Effect": "Allow",
      "Action": [
        "logs:DescribeLogGroups",
        "logs:DescribeLogStreams",
        "logs:GetLogEvents",
        "logs:FilterLogEvents",
        "logs:StartQuery",
        "logs:StopQuery",
        "logs:GetQueryResults"
      ],
      "Resource": "arn:aws:logs:eu-west-1:872515291723:log-group:/aws/lambda/*"
    }]
  }'
```

Verify: `make logs FN=crawler SINCE=2d` should now stream events.
