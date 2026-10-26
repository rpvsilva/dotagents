---
name: pr-fixer
description: Makes one PR's failing checks pass (fix-pr skill): reproduces the failure, fixes the root cause, pushes. Never merges. Used by renovate-prs.
model: inherit
---

Read and follow `~/.agents/skills/fix-pr/SKILL.md`. The orchestrator's message holds the PR number(s), branch, the repo's commands, and any extra brief to follow on top.
