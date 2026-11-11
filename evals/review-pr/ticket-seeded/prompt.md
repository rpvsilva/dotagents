You are a PR reviewer. Follow ~/.agents/skills/review-pr/reviewer-brief.md yourself: do the review in this session and start no subagent.

Review the whole ticket in PR #20, once and in depth. The worktree {work} is checked out at its head.

There is no GitHub and no git history in this checkout:
- read the PR facts in {input}/pr.md in place of `gh pr view`;
- read the diff in {input}/diff.patch, from the merge base of main and the head, in place of the merge-base command;
- skip the slow-suite comparison on the merge base, which this checkout lacks.

Read these, and take everything else from the code:
- {input}/ticket.md: the ticket the PR was asked to do

A change on main after the merge base is base drift, not a finding. Look across the ticket's tasks for what each one alone would miss: features that break each other, a contract changed on one side only. Run tests only after you have read the whole diff and each changed file in context.

Report file: {scratch}/report.md, the one file you may write. Report as the brief says.
