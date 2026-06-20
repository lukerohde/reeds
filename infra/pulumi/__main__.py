"""
reeds — Daily digest site
S3 + CloudFront front-end (via StaticSite component)
DynamoDB table + three Lambda functions + EventBridge schedules

DNS is resolved one of three ways (first match wins):
  1. reeds:parentIngressStack — Pulumi StackReference with a zone_id output
                                (aws-quill / shared ingress setup)
  2. reeds:zoneId             — Route53 hosted zone ID passed directly
                                (you already have a zone, just give us the ID)
  3. (neither)                — a new Route53 zone is created; nameservers are
                                exported so you can update your registrar

Required env vars (set in .env or GitHub secrets):
  ANTHROPIC_API_KEY  — Claude API key (digest Lambda)
  YOUTUBE_API_KEY    — YouTube Data API v3 key (youtube_crawler Lambda)
"""

import os
import json
import glob as _glob
import pulumi
import pulumi_aws as aws
from pulumi_static_site import StaticSite


def _lambda_archive(handler_dir: str, extra: dict | None = None) -> pulumi.AssetArchive:
    """Zip a Lambda handler directory + vendored packages from packages/ subdir.

    Run `make build-lambdas` before deploying to populate each handler's
    packages/ directory via `pip install -r requirements.txt -t packages/`.
    """
    base = os.path.abspath(os.path.join(os.path.dirname(__file__), handler_dir))
    assets: dict[str, pulumi.Asset] = {}

    # Packages installed by `make build-lambdas` — flattened to zip root
    packages_dir = os.path.join(base, 'packages')
    if os.path.isdir(packages_dir):
        for abs_path in _glob.glob(os.path.join(packages_dir, '**', '*'), recursive=True):
            if os.path.isfile(abs_path) and '__pycache__' not in abs_path and not abs_path.endswith('.pyc'):
                assets[os.path.relpath(abs_path, packages_dir)] = pulumi.FileAsset(abs_path)

    # Source files (added after packages so handler.py etc. always win)
    for abs_path in _glob.glob(os.path.join(base, '**', '*'), recursive=True):
        if (os.path.isfile(abs_path)
                and '__pycache__' not in abs_path
                and not abs_path.endswith('.pyc')
                and f'{os.sep}packages{os.sep}' not in abs_path):
            assets[os.path.relpath(abs_path, base)] = pulumi.FileAsset(abs_path)

    for dest, src in (extra or {}).items():
        assets[dest] = pulumi.FileAsset(os.path.abspath(os.path.join(os.path.dirname(__file__), src)))
    return pulumi.AssetArchive(assets)


config      = pulumi.Config()
domain_name = config.require("domainName")
bucket_name = config.require("bucketName")
aws_region  = pulumi.Config("aws").require("region")

# ── DNS / Route53 zone ────────────────────────────────────────────────────────
parent_stack_ref = config.get("parentIngressStack")
zone_id_direct   = config.get("zoneId")

if parent_stack_ref:
    # Path 1: shared ingress stack (aws-quill style)
    ingress = pulumi.StackReference(parent_stack_ref)
    zone_id = ingress.get_output("zone_id")
elif zone_id_direct:
    # Path 2: caller-supplied Route53 zone ID
    zone_id = zone_id_direct
else:
    # Path 3: standalone — create a new hosted zone
    _zone   = aws.route53.Zone("reeds-zone", name=domain_name)
    zone_id = _zone.zone_id
    pulumi.export("nameservers", _zone.name_servers)

# ── Static site (S3 + CloudFront + ACM + DNS) ─────────────────────────────────
site = StaticSite(
    "reeds",
    domain=domain_name,
    zone_id=zone_id,
    bucket_name=bucket_name,
    spa_mode=False,
)

# ── DynamoDB table ────────────────────────────────────────────────────────────
table = aws.dynamodb.Table(
    "articles",
    name="reeds-articles",
    billing_mode="PAY_PER_REQUEST",
    hash_key="url",
    attributes=[aws.dynamodb.TableAttributeArgs(name="url", type="S")],
)

# ── IAM role for Lambdas ──────────────────────────────────────────────────────
lambda_role = aws.iam.Role(
    "lambda-role",
    assume_role_policy=json.dumps({
        "Version": "2012-10-17",
        "Statement": [{"Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"}, "Action": "sts:AssumeRole"}],
    }),
)

aws.iam.RolePolicyAttachment("lambda-basic", role=lambda_role.name, policy_arn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole")

aws.iam.RolePolicy(
    "lambda-policy",
    role=lambda_role.id,
    policy=pulumi.Output.all(table.arn, site.bucket_name, site.distribution_id).apply(lambda args: json.dumps({
        "Version": "2012-10-17",
        "Statement": [
            {"Effect": "Allow", "Action": ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:Scan"], "Resource": args[0]},
            {"Effect": "Allow", "Action": ["s3:PutObject", "s3:GetObject"], "Resource": f"arn:aws:s3:::{args[1]}/*"},
            {"Effect": "Allow", "Action": ["cloudfront:CreateInvalidation"], "Resource": f"arn:aws:cloudfront::*:distribution/{args[2]}"},
        ],
    })),
)

# ── Lambda: crawler ───────────────────────────────────────────────────────────
crawler_zip = _lambda_archive("../../backend/crawler", {"config.yaml": "../../config/config.yaml"})

crawler = aws.lambda_.Function(
    "crawler",
    runtime="python3.12",
    handler="handler.handler",
    role=lambda_role.arn,
    code=crawler_zip,
    timeout=300,
    environment=aws.lambda_.FunctionEnvironmentArgs(variables={
        "DYNAMODB_TABLE": table.name,
    }),
)

# ── Lambda: digest ────────────────────────────────────────────────────────────
digest_zip = _lambda_archive("../../backend/digest", {"config.yaml": "../../config/config.yaml"})

digest = aws.lambda_.Function(
    "digest",
    runtime="python3.12",
    handler="handler.handler",
    role=lambda_role.arn,
    code=digest_zip,
    timeout=300,
    environment=aws.lambda_.FunctionEnvironmentArgs(variables={
        "DYNAMODB_TABLE":      table.name,
        "BUCKET_NAME":         site.bucket_name,
        "CF_DISTRIBUTION_ID":  site.distribution_id,
        "ANTHROPIC_API_KEY":   os.environ.get("ANTHROPIC_API_KEY", ""),
        "GOOGLE_API_KEY":      os.environ.get("GOOGLE_API_KEY", ""),
    }),
)

# ── Lambda: youtube_crawler ───────────────────────────────────────────────────
youtube_crawler_zip = _lambda_archive("../../backend/youtube_crawler", {"config.yaml": "../../config/config.yaml"})

youtube_crawler = aws.lambda_.Function(
    "youtube-crawler",
    runtime="python3.12",
    handler="handler.handler",
    role=lambda_role.arn,
    code=youtube_crawler_zip,
    timeout=120,
    environment=aws.lambda_.FunctionEnvironmentArgs(variables={
        "DYNAMODB_TABLE":  table.name,
        # TODO: set YOUTUBE_API_KEY in .env / GitHub secrets before enabling
        "YOUTUBE_API_KEY": os.environ.get("YOUTUBE_API_KEY", ""),
    }),
)

# ── EventBridge schedules (5am AEST = 7pm UTC) ───────────────────────────────
# crawler and youtube_crawler run together at 7pm UTC; digest runs 10 min later
crawler_schedule = aws.cloudwatch.EventRule("crawler-schedule", schedule_expression="cron(0 19 * * ? *)")
aws.cloudwatch.EventTarget("crawler-target", rule=crawler_schedule.name, arn=crawler.arn)
aws.lambda_.Permission("crawler-permission", action="lambda:InvokeFunction", function=crawler.name, principal="events.amazonaws.com", source_arn=crawler_schedule.arn)

youtube_crawler_schedule = aws.cloudwatch.EventRule("youtube-crawler-schedule", schedule_expression="cron(0 19 * * ? *)")
aws.cloudwatch.EventTarget("youtube-crawler-target", rule=youtube_crawler_schedule.name, arn=youtube_crawler.arn)
aws.lambda_.Permission("youtube-crawler-permission", action="lambda:InvokeFunction", function=youtube_crawler.name, principal="events.amazonaws.com", source_arn=youtube_crawler_schedule.arn)

digest_schedule = aws.cloudwatch.EventRule("digest-schedule", schedule_expression="cron(10 19 * * ? *)")
aws.cloudwatch.EventTarget("digest-target", rule=digest_schedule.name, arn=digest.arn)
aws.lambda_.Permission("digest-permission", action="lambda:InvokeFunction", function=digest.name, principal="events.amazonaws.com", source_arn=digest_schedule.arn)

# ── Outputs ───────────────────────────────────────────────────────────────────
pulumi.export("reeds_bucket",           site.bucket_name)
pulumi.export("reeds_distribution_id",  site.distribution_id)
pulumi.export("reeds_url",              site.distribution_domain.apply(lambda d: f"https://{d}"))
pulumi.export("dynamodb_table",         table.name)
