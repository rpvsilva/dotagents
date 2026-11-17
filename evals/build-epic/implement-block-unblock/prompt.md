Set up first, as one command: `mkdir -p {scratch}/reports && git -C {work} switch -q -c task/04-01 && git -C {work} config user.name epic-implementer && git -C {work} config user.email epic-implementer@localhost`. That puts the task's worktree on its new task branch, made from the ticket branch.

You are an epic-implementer. Follow ~/.agents/skills/build-epic/implementer-brief.md.
{
 "task": "{input}/tasks/04-01-block-unblock.md",
 "run": "{input}",
 "worktree": "{work}",
 "ticket_branch": "main",
 "test_command": "export CAPPED_MB=\"${CAPPED_MB:-2560}\" CAPPED_SECS=\"${CAPPED_SECS:-300}\" && ~/.agents/bin/capped -- python3 -I -m pytest -q -c pyproject.toml",
 "output": "{scratch}/reports/04-01.json"
}

This run has no git remote and no GitHub: the ticket branch is the local `main`, and nothing moves it while you work. In step 5, skip `git fetch origin`: your branch already holds the ticket branch's tip.
