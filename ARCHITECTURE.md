# Reeds — Deployed Architecture

```mermaid
flowchart TB
    %% ── External sources ─────────────────────────────────────────────────────
    subgraph sources["RSS Sources (10 blogs)"]
        RSS["Simon Willison · Andrej Karpathy · Martin Fowler\nCharity Majors · Thorsten Ball · Kent Beck\nHenrik Kniberg · Steve Yegge · Addy Osmani · Bryan Cantrill"]
    end

    %% ── AWS scheduled pipeline ───────────────────────────────────────────────
    subgraph aws["AWS — eu-west-1"]

        subgraph schedule["EventBridge (2 daily schedules)"]
            EB_CRAWL["Crawler schedule"]
            EB_DIGEST["Digest schedule"]
        end

        subgraph lambdas["Lambda"]
            CRAWLER["crawler\n① parse RSS feeds\n② HTTP fetch article text\n③ put_item → DynamoDB"]
            DIGEST["digest\n① scan unserved articles\n② relevance filter  (AI)\n③ summarise each  (AI)\n④ curate top 10   (AI)\n⑤ render HTML → S3\n⑥ invalidate CloudFront\n⑦ mark articles served"]
        end

        DDB[("DynamoDB\nreeds-articles\nPAY_PER_REQUEST\nkey: url")]

        subgraph delivery["Delivery"]
            S3[("S3 Bucket\nlukerohde-reeds\n─────────────────\npublic/  ← static assets\ndigest/{date}/index.html\ndigest/latest/index.html")]
            CF["CloudFront\nreeds.lukeroh.de\n+ ACM certificate"]
        end
    end

    %% ── External AI ──────────────────────────────────────────────────────────
    subgraph ai["Anthropic Claude API"]
        HAIKU["claude-haiku-4-5\nrelevance check\n(yes / no)"]
        SONNET["claude-sonnet-4-6\nsummarise + curate"]
    end

    %% ── CI/CD ────────────────────────────────────────────────────────────────
    subgraph cicd["CI/CD (GitHub Actions)"]
        GH["GitHub — main branch"]
        GHA_INFRA["deploy-infra.yml\n(infra/pulumi · backend · config)"]
        GHA_SITE["deploy-site.yml\n(public/)"]
        PULUMI["Pulumi Cloud\nstack: lukerohde-reeds/prod\n(state + secrets)"]
        INGRESS["Parent ingress stack\nlukerohde-ingress/prod\n(provides OIDC role ARNs)"]
    end

    USER((("End User")))

    %% ── Crawl flow ───────────────────────────────────────────────────────────
    EB_CRAWL -->|daily trigger| CRAWLER
    CRAWLER -->|"HTTP GET (feedparser + requests)"| sources
    CRAWLER -->|"put_item (new articles only)"| DDB

    %% ── Digest flow ──────────────────────────────────────────────────────────
    EB_DIGEST -->|daily trigger| DIGEST
    DIGEST -->|"scan (served_date = '')"| DDB
    DIGEST -->|"is this relevant?"| HAIKU
    DIGEST -->|"summarise + curate top 10"| SONNET
    DIGEST -->|"put_object (HTML)"| S3
    DIGEST -->|"create_invalidation"| CF
    DIGEST -->|"update served_date"| DDB

    %% ── Serving ──────────────────────────────────────────────────────────────
    S3 -->|origin| CF
    CF -->|"HTTPS / cached"| USER

    %% ── CI/CD flow ───────────────────────────────────────────────────────────
    GH -->|push to main| GHA_INFRA
    GH -->|push to main| GHA_SITE
    GHA_INFRA -->|"pulumi up\n(build-lambdas first)"| PULUMI
    PULUMI -->|"manages all AWS resources"| aws
    GHA_SITE -->|"s3 sync public/\n+ CF invalidation"| S3
    GHA_INFRA -->|OIDC assume role| INGRESS
    GHA_SITE -->|OIDC assume role| INGRESS
    INGRESS -->|"grants AWS access"| aws
```

## Data flow summary

| Stage | From | To | What |
|---|---|---|---|
| **Extract** | EventBridge | Crawler Lambda | daily cron |
| | RSS feeds | Crawler Lambda | feed entries + article HTML |
| | Crawler Lambda | DynamoDB | articles (url, title, content, author, dates) |
| **Transform** | EventBridge | Digest Lambda | daily cron |
| | DynamoDB | Digest Lambda | unserved articles |
| | Digest Lambda | Claude Haiku | relevance check per article |
| | Digest Lambda | Claude Sonnet | summary per relevant article |
| | Digest Lambda | Claude Sonnet | curate top 10 from pool |
| | Digest Lambda | DynamoDB | write `status`, `summary`, `served_date` |
| **Load** | Digest Lambda | S3 | `digest/{date}/index.html` + `latest/` |
| | Digest Lambda | CloudFront | cache invalidation |
| **Serve** | S3 | CloudFront | origin |
| | CloudFront | User | cached HTTPS at `reeds.lukeroh.de` |

## Cost profile

| Resource | Tier |
|---|---|
| Lambda (2 functions, ~60 invocations/month) | Free tier |
| DynamoDB (PAY_PER_REQUEST) | Free tier |
| EventBridge (2 schedules) | Free |
| S3 (~10 KB HTML) | ~$0.001/month |
| CloudFront | Free tier |
| ACM certificate | Free |

**Target: < $1/month** (AI API calls are the dominant cost)
