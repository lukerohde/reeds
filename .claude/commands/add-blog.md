# /add-blog — add a new blog to the reeds digest

Discover, verify, add, and ship a new blog source. Do the work — don't just describe it.

---

## Step 1 — Get the blog URL

Ask: "What's the URL of the blog you want to add? (homepage is fine)"

Store as BLOG_URL.

---

## Step 2 — Discover and verify the feed

Run:
```bash
make test-feed FEED=<BLOG_URL>
```

Show the full output.

**If it fails (❌):**
- Ask the user if they know the feed URL directly
- If yes, run `make test-feed FEED=<that url>` and continue
- If no, stop and tell the user the site doesn't appear to publish an RSS/Atom feed

**If it succeeds (✅):**
- Note the feed URL from the output (line starting with `✅  Feed:`)
- Ask: "What name should appear in the digest for this author?" 
- Store as AUTHOR_NAME

---

## Step 3 — Add to config/config.yaml

Read `config/config.yaml`, append to the `blogs:` list:

```yaml
  - author: <AUTHOR_NAME>
    url: <BLOG_URL stripped of trailing slash>
    feed: <FEED_URL>
```

---

## Step 4 — Run tests

```bash
make test
```

If tests fail, investigate and fix before continuing.

---

## Step 5 — Commit and push

```bash
git add config/config.yaml
git commit -m "add <AUTHOR_NAME> to reeds digest"
git push
```

Show the push output. Then tell the user:

```
✅  Done. <AUTHOR_NAME> is live — their posts will appear in tomorrow's digest.

CI is deploying now: https://github.com/<owner>/<repo>/actions

No infra deploy needed — the Lambda reads config.yaml from the zip,
so the next scheduled crawl (7pm UTC / 5am AEST) picks up the new feed automatically.
```

---

## Notes

- The digest curates the top 10 from up to 20 recent unserved articles across all blogs —
  a new author competes on quality, not just recency
- If you want to preview them immediately: `make local-crawl` then `make dev`
