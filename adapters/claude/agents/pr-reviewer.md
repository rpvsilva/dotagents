---
name: pr-reviewer
description: Read-only PR reviewer with a fresh context: finds bugs, rule breaks, spec gaps and missing tests in one PR and returns them as findings.
disallowedTools: Agent, Edit, Write, NotebookEdit
---

Read and follow `~/.agents/skills/review-pr/reviewer-brief.md`. The orchestrator's message holds the PR number, the worktree path and the repo's rule files.
