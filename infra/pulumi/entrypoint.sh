#!/bin/bash
set -e

# ── State backend ──────────────────────────────────────────────────────────────
# Pulumi Cloud (default, free for personal use) — set PULUMI_ACCESS_TOKEN in .env
# Local fallback: set PULUMI_BACKEND_URL=file:///infra/.pulumi
if [ -n "$PULUMI_BACKEND_URL" ]; then
    pulumi login "$PULUMI_BACKEND_URL"
elif [ -n "$PULUMI_ACCESS_TOKEN" ]; then
    pulumi login
else
    echo "❌  PULUMI_ACCESS_TOKEN not set — see .env.example" >&2
    exit 1
fi

# ── Stack selection ────────────────────────────────────────────────────────────
STACK="${PULUMI_STACK:-prod}"
pulumi stack select "$STACK" 2>/dev/null || pulumi stack init "$STACK"

exec pulumi "$@"
