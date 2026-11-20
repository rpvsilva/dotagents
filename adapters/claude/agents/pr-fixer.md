---
name: pr-fixer
description: Takes one PR's failing checks from red to green and pushes; never merges. Start one per PR to fix.
model: sonnet
isolation: worktree
disallowedTools: Agent
---

Read and follow `~/.claude/skills/fix-pr/SKILL.md`. The orchestrator's message holds the PR number(s), branch, the repo's commands, and any extra brief to follow on top.
