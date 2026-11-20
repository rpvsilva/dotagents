---
name: pr-reviewer
description: "PR reviewer with a fresh context: finds bugs, rule breaks, spec gaps and missing tests in one PR; never edits the repo, writes only its output file."
model: sonnet
disallowedTools: Agent, Edit, NotebookEdit
---

Read and follow `~/.claude/skills/review-pr/reviewer-brief.md`. The orchestrator's message is the prompt `review_prompt.py` printed: one line naming the brief, then your input object as JSON.
