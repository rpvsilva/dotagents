---
name: security-reviewer
description: Read-only security reviewer with a fresh context: finds injection, leaked secrets, authz gaps, excess privilege and supply-chain risks in one PR and returns them as findings.
model: inherit
readonly: true
---

Read and follow `~/.agents/skills/review-pr/security-brief.md`. The orchestrator's message holds the PR number, the worktree path and the repo's rule files.
