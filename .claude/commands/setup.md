# /setup — reeds installer

Walk the user through setting up their own reeds digest.
By the end they will have:
- A live digest site at their chosen subdomain
- DynamoDB table, two Lambda functions, EventBridge schedules all deployed
- GitHub repo with CI/CD

**Important:** Be conversational. Explain WHY each step matters.
Check actual state before asking — don't ask if Docker is installed if `docker info` already works.

**Secret safety — follow these rules throughout:**
- Never run a command that prints a secret value to stdout (no `cat .env`, no `echo $TOKEN`)
- Never expand a secret into a `--body` argument — use stdin pipes instead
- Check whether a key is populated with `grep -qE '^KEY=.+' .env` — this confirms presence without revealing the value
- When setting GitHub secrets: `grep -m1 '^KEY=' .env | cut -d= -f2- | gh secret set KEY`
- Always ask the user to open `.env` in their own editor to fill in values — never ask them to paste secrets into the chat

---

## Step 0 — Orient the user

First, ask whether they have an existing aws-quill-based project:

"Do you have an existing aws-quill-based project with its own Route53 hosted zone?
(If you're not sure, the answer is probably no.)"

Store the answer — it determines the DNS setup path later.

Then show the plan:

```
Here's what we'll do together:

  1. ✅  Check prerequisites (Docker, gh CLI, AWS)
  2. 🔑  Verify AWS credentials
  3. ☁️   Verify Pulumi Cloud access
  4. 🌐  Collect config (domain, DNS setup, API keys, etc.)
  5. ⚙️   Write config files
  6. 🏗️   Deploy reeds infra (DynamoDB + Lambda + EventBridge + S3 + CloudFront)
  7. 📦  Create GitHub repo + set CI secrets
  8. 🕷️   Run first crawl
  9. 📰  Generate first digest
  10. 🔗  (Optional) Add reeds link to your parent site navigation

Run /teardown at any time to destroy reeds infrastructure.
```

---

## Step 1 — Check prerequisites

```bash
docker info > /dev/null 2>&1 && echo "✅ Docker" || echo "❌ Docker not running"
gh --version 2>/dev/null && echo "✅ gh CLI" || echo "❌ gh CLI not found"
docker compose run --rm awscli --version 2>&1 | grep -q "aws-cli" && echo "✅ AWS CLI (Docker)" || echo "❌ AWS CLI (Docker)"
```

Stop if Docker is not running — it's required for everything else.

If gh CLI is missing, offer to install:
```bash
brew install gh   # macOS
```
Or direct to https://cli.github.com for other platforms.

---

## Step 2 — GitHub authentication

```bash
gh auth status 2>/dev/null && echo "✅ gh authenticated" || echo "⚠️  Not authenticated"
```

If not authenticated:
```bash
gh auth login
```

After login, store the GitHub username:
```bash
gh api user --jq '.login'
```
Store as GITHUB_OWNER.

---

## Step 3 — AWS credentials

First ensure `.env` exists:
```bash
test -f .env && echo "✅ .env exists" || echo "⚠️  missing — will create from example"
```

If missing:
```bash
cp .env.example .env
```
Then tell the user: "Open `.env` in your editor and fill in `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY`. Don't paste the values here."

Check the keys are populated (without reading their values):
```bash
grep -qE '^AWS_ACCESS_KEY_ID=.+' .env    && echo "✅ AWS_ACCESS_KEY_ID set"    || echo "❌ AWS_ACCESS_KEY_ID missing"
grep -qE '^AWS_SECRET_ACCESS_KEY=.+' .env && echo "✅ AWS_SECRET_ACCESS_KEY set" || echo "❌ AWS_SECRET_ACCESS_KEY missing"
```

Once both are confirmed set, verify they actually work:
```bash
docker compose run --rm awscli sts get-caller-identity
```

Show the account ID so the user can confirm it's the right AWS account.

---

## Step 4 — Pulumi Cloud

Check the token:
```bash
grep -qE '^PULUMI_ACCESS_TOKEN=.+' .env && echo "✅ PULUMI_ACCESS_TOKEN set" || echo "❌ missing — add to .env"
```

If missing, guide them to https://app.pulumi.com to create an access token, then:
"Open `.env` in your editor and set `PULUMI_ACCESS_TOKEN`. Don't paste it here."

Get their Pulumi org:
```bash
docker compose run --rm pulumi pulumi whoami
```
Store the output as PULUMI_ORG.

---

## Step 5 — Collect configuration

Ask each question in order.

**a) Subdomain for reeds**
"What subdomain will reeds live at? (e.g. reads.example.com)"
Store as DOMAIN.

**b) DNS setup** — branch based on answer from Step 0:

**If aws-quill parent project:**
"What is the Pulumi stack reference for your parent project's ingress?
Format: <org>/<stack-name>/prod  (e.g. myorg/myproject-ingress/prod)"

Verify it exists and has a zone_id output:
```bash
docker compose run --rm pulumi pulumi stack output zone_id -s <PARENT_INGRESS_STACK>
```
If this fails, help them find the right stack:
```bash
docker compose run --rm pulumi pulumi stack ls --all
```
Store as PARENT_INGRESS_STACK.

**If standalone — existing Route53 zone:**
"Do you already have a Route53 hosted zone for this domain? If so, what is the Zone ID?
(Find it in the AWS Console under Route53 → Hosted zones. Looks like Z1234567890ABCDEF)"

If they have one, store as ZONE_ID.
If they don't, that's fine — reeds will create one and we'll configure the nameservers after deploy.

**c) GitHub repo name**
"What do you want to call this repo? (default: reeds)"
Store as REPO_NAME.

**d) AWS region**
"Which AWS region? (default: eu-west-1)"
Store as AWS_REGION.

**e) Anthropic API key**
Check if it's already set:
```bash
grep -qE '^ANTHROPIC_API_KEY=.+' .env && echo "✅ ANTHROPIC_API_KEY set" || echo "❌ missing"
```
If missing: "Open `.env` in your editor and add your Anthropic API key. Get one at https://console.anthropic.com"

**f) S3 bucket name**
Set as `<GITHUB_OWNER>-reeds` (e.g. `janedoe-reads`). S3 bucket names are globally unique —
this scoping avoids conflicts. Store as BUCKET_NAME.

---

## Step 6 — Write config files

**Write infra/pulumi/Pulumi.prod.yaml** (replacing whatever is there):

For aws-quill path:
```yaml
config:
  aws:region: <AWS_REGION>
  <GITHUB_OWNER>-reeds:domainName: <DOMAIN>
  <GITHUB_OWNER>-reeds:bucketName: <BUCKET_NAME>
  <GITHUB_OWNER>-reeds:parentIngressStack: <PARENT_INGRESS_STACK>
```

For standalone with existing zone:
```yaml
config:
  aws:region: <AWS_REGION>
  <GITHUB_OWNER>-reeds:domainName: <DOMAIN>
  <GITHUB_OWNER>-reeds:bucketName: <BUCKET_NAME>
  <GITHUB_OWNER>-reeds:zoneId: <ZONE_ID>
```

For standalone without zone (reads will create one):
```yaml
config:
  aws:region: <AWS_REGION>
  <GITHUB_OWNER>-reeds:domainName: <DOMAIN>
  <GITHUB_OWNER>-reeds:bucketName: <BUCKET_NAME>
```

**Write infra/pulumi/Pulumi.yaml** — set the stack name:
```yaml
name: <GITHUB_OWNER>-reeds
runtime: python
description: <DOMAIN> — daily digest site
```

**Patch .github/workflows/deploy-infra.yml** for the right CI auth approach:

For aws-quill path — replace the existing ingress stack reference in the workflow:
Find lines referencing the current ingress stack (grep for `ingress`) and replace with
`<PARENT_INGRESS_STACK>`. Also replace the reeds stack reference similarly.

For standalone path — the workflow uses OIDC from the parent ingress, which doesn't exist.
Replace the `Get infra role ARN` step and `Configure AWS credentials (OIDC)` step with:
```yaml
      - name: Configure AWS credentials
        uses: aws-actions/configure-aws-credentials@v4
        with:
          aws-access-key-id: ${{ secrets.AWS_ACCESS_KEY_ID }}
          aws-secret-access-key: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
          aws-region: <AWS_REGION>
```

**Write AWS region to .env:**
```
AWS_DEFAULT_REGION=<AWS_REGION>
```

Show a summary of all changed files before proceeding.

---

## Step 7 — Deploy reeds infra

**Why:** Creates the S3 bucket, CloudFront distribution, ACM certificate, DynamoDB table,
two Lambda functions, and EventBridge schedules. Also creates or attaches to the DNS zone.

```bash
make infra-up
```

This first runs `make build-lambdas` (pip-installs handler dependencies into the Lambda zips),
then deploys via Pulumi. Takes 5-15 minutes (CloudFront + ACM certificate validation is the slow part).

**Standalone without existing zone:** After deploy, run:
```bash
make infra-outputs
```
Look for the `nameservers` output — these are four AWS nameserver addresses.
Tell the user:
"Go to your domain registrar and add these four NS records for <DOMAIN> (or its parent domain).
DNS propagation takes a few minutes to a few hours. Once done, the site will be reachable."

On success, capture the outputs:
```bash
make infra-outputs
```

Store `reeds_bucket` as SITE_BUCKET and `reeds_distribution_id` as CF_DISTRIBUTION_ID.
Update `.env`:
```
BUCKET_NAME=<SITE_BUCKET>
CF_DISTRIBUTION_ID=<CF_DISTRIBUTION_ID>
```

---

## Step 8 — Create GitHub repo and set CI secrets

**a) Init git and create repo:**
```bash
git init
git add -A
git commit -m "Initial reeds setup for <DOMAIN>"
gh repo create <GITHUB_OWNER>/<REPO_NAME> --private --description "Daily reading digest at <DOMAIN>" --source=. --remote=origin
```

(Private is a sensible default; offer --public if they prefer.)

**b) Set CI secrets:**

Always required:
```bash
grep -m1 '^PULUMI_ACCESS_TOKEN=' .env | cut -d= -f2- \
  | gh secret set PULUMI_ACCESS_TOKEN --repo <GITHUB_OWNER>/<REPO_NAME>

grep -m1 '^ANTHROPIC_API_KEY=' .env | cut -d= -f2- \
  | gh secret set ANTHROPIC_API_KEY --repo <GITHUB_OWNER>/<REPO_NAME>
```

Standalone path only (OIDC not available):
```bash
grep -m1 '^AWS_ACCESS_KEY_ID=' .env | cut -d= -f2- \
  | gh secret set AWS_ACCESS_KEY_ID --repo <GITHUB_OWNER>/<REPO_NAME>

grep -m1 '^AWS_SECRET_ACCESS_KEY=' .env | cut -d= -f2- \
  | gh secret set AWS_SECRET_ACCESS_KEY --repo <GITHUB_OWNER>/<REPO_NAME>
```

**c) Push:**
```bash
git push -u origin main
```

Show CI URL: `https://github.com/<GITHUB_OWNER>/<REPO_NAME>/actions`

---

## Step 9 — Deploy static assets and run first crawl

Deploy the landing page:
```bash
make deploy
```

Run the crawler:
```bash
make crawl
```

Show the output (number of articles added).

Then run the digest:
```bash
make digest
```

Show the output. If successful, the digest is now live at:
`https://<DOMAIN>/digest/latest/`

---

## Step 10 — (Optional) Add navigation link to parent site

If they have a parent site, suggest adding a link to reads from its navigation.
Ask: "Would you like to add a reeds link to your parent site's navigation?"

If yes, ask what framework/site generator they use and help them find the right file.
The link should point to `https://<DOMAIN>`.

---

## Step 11 — Customise blogs (optional)

Tell the user:

```
reeds comes with 10 tech blogs pre-configured. To add or remove sources:
  - Edit config/config.yaml (blogs section)
  - Or run /add-blog to have me discover, test, and add a new feed for you
```

---

## Step 12 — Summary

```
✅  reeds is live at https://<DOMAIN>

What was deployed:
  - S3 bucket: <SITE_BUCKET>
  - CloudFront distribution: <CF_DISTRIBUTION_ID>
  - DynamoDB table: reeds-articles
  - Lambda: crawler  (runs daily at 7pm UTC / 5am AEST)
  - Lambda: digest   (runs daily at 7:10pm UTC)

Day-to-day:
  make crawl          — run crawler manually
  make digest         — run digest manually
  make local-crawl    — crawl locally with LocalStack (no AWS needed)
  make dev            — preview digest locally (opens in browser)
  /add-blog           — discover, test, and add a new blog source
  make infra-up       — redeploy infra (needed after changes to backend/)
  git push            — CI deploys automatically on push to main

To tear everything down:
  /teardown
```
