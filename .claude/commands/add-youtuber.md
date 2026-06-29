# /add-youtuber — add a new YouTube channel to the reeds digest

Resolve, verify, add, and ship a new YouTube source. Do the work — don't just describe it.

---

## Step 1 — Get the channel handle or URL

Ask: "What's the YouTube channel handle (e.g. `@NateBJones`) or URL?"

Store as HANDLE. A `@handle`, a channel URL, or a `UC…` channel ID all work.

---

## Step 2 — Resolve and add to config

Run:
```bash
make add-youtuber HANDLE=<HANDLE>
```

This resolves the handle to its `UC…` channel ID straight from the page (no API key needed)
and appends a `{name, channel_id}` entry to the `youtubers:` list in `config/config.yaml`.

Show the full output, then:

- **`✓  added <name> → <channel_id>`** — continue to Step 3.
- **`•  <name> → <channel_id> already configured`** — the channel is already a source. Tell the
  user, and stop (nothing to commit).
- **`✗  <handle>: …`** — resolution failed (bad handle / no channel ID on the page). Ask the user
  to double-check the handle or paste the channel URL, then retry.

---

## Step 3 — Run tests

```bash
make test
```

If tests fail, investigate and fix before continuing.

---

## Step 4 — Commit and push

```bash
git add config/config.yaml
git commit -m "add <name> (YouTube) to reeds digest"
git push
```

Show the push output. Then tell the user:

```
✅  Done. <name> is live — their videos will appear in tomorrow's digest.

CI is deploying now: https://github.com/<owner>/<repo>/actions

No infra deploy needed — the Lambda reads config.yaml from the zip,
so the next scheduled crawl picks up the new channel automatically.
```

---

## Notes

- Channel videos are crawled when `YOUTUBE_API_KEY` is set; transcripts are fetched per video,
  with a Gemini fallback (`GOOGLE_API_KEY`) for videos whose transcript can't be retrieved.
- The digest curates the top articles across all sources, so a new channel competes on quality,
  not just recency.
- To preview immediately: `make local-crawl` then `make local-preview`.
