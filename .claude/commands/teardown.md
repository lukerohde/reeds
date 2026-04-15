# /teardown — Destroy reeds infrastructure

⚠️  This destroys:
- The reeds CloudFront distribution
- The reeds S3 bucket (and all digest HTML stored there)
- The reeds ACM certificate
- The DNS record for the reeds subdomain (e.g. reeds.lukeroh.de)
- The DynamoDB table (and all crawled article data)
- The crawler and digest Lambda functions
- The EventBridge schedules

**Not touched:**
- The parent project's Route53 zone, ingress stack, or any other infrastructure
- This Git repo and all your code

You can redeploy everything with `/setup` after teardown.

---

## Step 1 — Confirm

Ask clearly:

"This will destroy all reeds infrastructure. The digest site will go offline.
Your code is safe — only the AWS resources will be deleted.

Are you sure? Type YES to continue."

Only proceed if they type exactly `YES`.

---

## Step 2 — Empty the S3 bucket

Pulumi cannot delete a non-empty S3 bucket. Empty it first:

```bash
BUCKET=$(docker compose run --rm -T pulumi stack output reeds_bucket 2>/dev/null | tail -1)
docker compose run --rm -e AWS_DEFAULT_REGION=$(grep -m1 'aws:region' infra/pulumi/Pulumi.prod.yaml | awk '{print $2}') \
  awscli s3 rm s3://$BUCKET --recursive
```

**Pass:** All objects deleted.
**Skip:** If the bucket is already empty or doesn't exist, continue.

---

## Step 3 — Destroy reeds infra

```bash
make infra-destroy
```

Watch for errors. If Pulumi reports resources "in use" or dependency issues, help diagnose before continuing.

This destroys everything in the reeds Pulumi stack:
DynamoDB, Lambdas, EventBridge rules, S3 bucket, CloudFront, ACM certificate,
and the Route53 record for the reeds subdomain.

The parent project's Route53 hosted zone is **not affected**.

---

## Step 4 — Clean up local config (optional)

Ask: "Do you want to remove `.env`? (Your API keys and tokens will be deleted from this machine.)"

If yes:
```bash
rm .env
echo "✅  .env removed"
```

---

## Step 5 — Remove nav link from parent site (optional)

Remind the user:

"If you added a reeds link to your parent site's navigation, you may want to remove it now.
Otherwise visitors will click a link that goes nowhere."

---

## Step 6 — Confirm and summarise

```
✅  All reeds infrastructure destroyed.

What's gone:
  - CloudFront distribution and S3 bucket
  - ACM certificate
  - DynamoDB table (all article data)
  - Lambda functions and EventBridge schedules
  - DNS record for <DOMAIN>

What's still here:
  - This Git repo and all your code
  - Your GitHub repo (to delete: gh repo delete <GITHUB_OWNER>/<REPO_NAME>)
  - The parent project's Route53 zone and ingress (untouched)

To redeploy from scratch: /setup
```
