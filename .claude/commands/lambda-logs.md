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

### IAM grant (provisioned by Pulumi)

The required CloudWatch Logs read is granted by Pulumi as an `aws.iam.UserPolicy`
(`reeds-logs-read`) attached to the user named in `reeds:logsReaderUser`
(`infra/pulumi/__main__.py`). It's applied on `make infra-up`. Installs that deploy
via OIDC only (no long-lived user) just omit that config key.

If you ever need to grant it by hand (e.g. before the first deploy), the equivalent is:

```bash
aws iam put-user-policy \
  --user-name lukerohde-pulumi-user \
  --policy-name reeds-logs-read \
  --policy-document '{
    "Version": "2012-10-17",
    "Statement": [
      {"Effect": "Allow", "Action": "logs:DescribeLogGroups", "Resource": "*"},
      {"Effect": "Allow",
       "Action": ["logs:DescribeLogStreams","logs:GetLogEvents","logs:FilterLogEvents"],
       "Resource": "arn:aws:logs:eu-west-1:*:log-group:/aws/lambda/*"}
    ]
  }'
```

Verify: `make logs FN=crawler SINCE=2d` streams events.
