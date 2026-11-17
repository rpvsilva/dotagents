You are a pr-reviewer. Follow ~/.agents/skills/review-pr/reviewer-brief.md yourself: do the review in this session and start no subagent.
{
 "mode": "task",
 "pr": 12,
 "worktree": "{work}",
 "head": "HEAD",
 "base": "main",
 "diff": "cat {input}/diff.patch",
 "pr_facts": "{input}/pr.md",
 "task": "{input}/task.md",
 "output": "{scratch}/report.json",
 "remote": true
}
