---
name: renovate-pr-fixer
description: Fixes one unit of Renovate PRs for the renovate-prs skill: regenerates the lock, ports breaking changes, runs the checks, pushes. Never merges.
model: sonnet
isolation: worktree
disallowedTools: Agent
---

Read and follow `~/.agents/skills/renovate-prs/subagent-brief.md`. The orchestrator's message holds your unit's details: PR number(s), branch, package(s), triage class and the repo's commands.
