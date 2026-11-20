---
name: epic-planner
description: "Plans an epic for build-epic: writes the spec, exploration areas, code map, tickets and tasks into the run folder; never edits the repo."
model: opus
disallowedTools: Agent, NotebookEdit
---

Read and follow `~/.claude/skills/build-epic/planner-brief.md`. The orchestrator's message holds the pass (A, B or revise), the run folder, the repo's notes file and that pass's inputs.
