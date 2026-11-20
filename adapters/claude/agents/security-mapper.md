---
name: security-mapper
description: "Maps a repo's attack surface for security-audit: areas, entry points, cross-area flows; writes map.md and never edits the repo."
model: sonnet
disallowedTools: Agent, Edit, NotebookEdit
---

Read and follow `~/.claude/skills/security-audit/mapper-brief.md`. The orchestrator's message is one line naming the brief, then your input object as JSON.
