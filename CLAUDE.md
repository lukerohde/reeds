# CLAUDE.md

[`AGENTS.md`](AGENTS.md) is the single source of truth for this project —
architecture, config, the ETL pipeline, pluggable sources (blogs + YouTube),
Lambda packaging, testing, deployment, and cost. **Read it first.**

This file exists only to point Claude Code at that guide and to index the
project's slash commands.

## Slash commands

Defined in [`.claude/commands/`](.claude/commands/) — run `/<name>` in Claude Code:

| Command | Purpose |
|---|---|
| `/setup` | Walk through installing your own reeds digest end-to-end |
| `/add-blog` | Discover, verify, add, and ship a new blog source |
| `/check-localstack` | Verify LocalStack is up and initialised before local dev |
| `/test-integration` | Run digest integration tests against LocalStack |
| `/test-all` | Full suite: unit, integration, infra health, manual checks |
| `/verify-infra` | Smoke-test deployed AWS infra (rules, Lambdas, permissions, startup) |
| `/diagnose-author` | DynamoDB stats for one author (served/unserved, pool position) |
| `/lambda-logs` | Read crawler/digest Lambda execution logs (invoke-tail or CloudWatch) |
| `/teardown` | Destroy all reeds infrastructure cleanly (empties S3 first) |
