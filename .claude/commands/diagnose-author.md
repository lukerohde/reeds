# /diagnose-author — Show DynamoDB article stats for a specific author

Queries the DynamoDB table for a specific author's articles and reports total/served/unserved
counts, status breakdown, position in the unserved candidates pool, and per-day serve history.

Use this to diagnose underrepresentation (an author rarely appears in digests) or
overrepresentation (one author dominates every digest).

**Requires:** `DYNAMODB_TABLE` in `.env`, AWS credentials

---

## Step 1 — Run the diagnosis

```bash
make diagnose-author AUTHOR="<author name>"
```

Use the exact author name as stored in DynamoDB (matches the `author` field in `config/config.yaml`).

Examples:
```bash
make diagnose-author AUTHOR="Simon Willison"
make diagnose-author AUTHOR="Martin Fowler"
```

---

## Step 2 — Interpret the output

**Key metrics to check:**

| Metric | What it means |
|---|---|
| `unserved` count | Author's backlog — high number means articles are accumulating unserved |
| `in top-20 (candidates pool)` | How many of this author's articles are in the pool right now |
| `unserved status breakdown` | `unprocessed` = not yet AI-classified; `ignored` = relevance-filtered out |
| Per-day served count | How often this author appears per digest |

**Signs of underrepresentation:**
- High unserved count but only 0–1 served per day
- Author rarely appears in the top-20 unserved (feed may not be crawling recently)
- Many `ignored` articles (relevance prompt may be filtering the author's style)

**Signs of overrepresentation (firehose author problem):**
- Author has 10+ articles in the top-20 unserved queue
- Other authors cannot reach the candidates pool at all
- This is the Simon Willison problem — solved by `max_per_author` in `config/config.yaml`

---

## Step 3 — Suggest fixes based on findings

**Underrepresentation:**
- If many `ignored`: check `prompts.relevance_check` in `config/config.yaml` — may be too strict
- If few unprocessed: the feed may not be crawling — run `make crawl` and re-diagnose
- If low unserved count overall: all articles are served; normal behaviour

**Overrepresentation:**
- Lower `max_per_author` in `config/config.yaml` (currently 2) — or keep it and accept
- Increase `candidates_pool` to let more non-dominant authors through
- The firehose author will still appear (up to `max_per_author` per digest), just not dominate

---

## Step 4 — Cross-check across all authors

To see all authors at once, run:
```bash
make diagnose-author AUTHOR="Simon Willison"
make diagnose-author AUTHOR="Martin Fowler"
# etc.
```

Or query the full unserved queue breakdown manually:
```bash
docker compose run --rm \
  -e DYNAMODB_TABLE=$(grep DYNAMODB_TABLE .env | cut -d= -f2-) \
  -e AWS_DEFAULT_REGION=$(grep -m1 'aws:region:' infra/pulumi/Pulumi.prod.yaml | awk '{print $2}') \
  crawler python -c "
import os, boto3
from collections import Counter
table = boto3.resource('dynamodb').Table(os.environ['DYNAMODB_TABLE'])
items = table.scan()['Items']
unserved = [i for i in items if i.get('served_date','') == '']
unserved.sort(key=lambda x: x.get('published_date',''), reverse=True)
print(Counter(i.get('author') for i in unserved[:20]))
"
```

This shows the author distribution in the current candidates pool.
