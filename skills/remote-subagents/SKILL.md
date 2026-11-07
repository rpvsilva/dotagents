---
name: remote-subagents
description: Run a subagent's brief on the remote backend (a cluster, Docker or an SSH host) instead of this machine, and act on its outcome. Use when a skill marks a subagent remote-capable, or the human asks to run a brief remotely.
---

# Remote subagents

A subagent the calling skill marks **remote-capable** (it only changes its worktree), or one the human asks to run remotely, runs on the remote backend (the `remote.backend` Setting: a cluster, Docker or an SSH host) instead of this machine when `remote-agent ready` exits 0 in the repo and the human did not ask for `--local`. `ready` prints the backend, or why remote runs are off: no backend set, no token, the repo sets `remote.allowed` to `false`, or the backend does not answer (with its error). Say that reason once, then run subagents locally.

The backend is compute only: it gets this machine's setup, a snapshot of the worktree and the files you attach, and gives back a patch. It has no GitHub access and no git branches, so it cannot push, open a PR, read CI logs or see another branch. Details: [remote/README.md](../../remote/README.md).

`remote-agent` is `~/.agents/bin/remote-agent`, unless the calling skill names another path to dispatch with; then use that path, with the same arguments. When `ready` says nothing is set up, the human sets it up with `~/.agents/bin/remote-agent setup` in their terminal (it asks for the token) and proves it with `remote-agent check`. When the human asks to turn remote off or change it, follow [configure](../configure/SKILL.md) if it is installed, else `~/.agents/bin/settings set remote <key> <value> --machine` (or `--repo` for this repo only).

Each remote run takes a **remote slot**, not a local one: at most `~/.agents/bin/settings get remote max_sandboxes` remote runs at once, whatever the skill.

### Steps

1. Create the worktree as for a local subagent: a git worktree on the subagent's own branch.
2. Write the message you would hand the subagent into a file. Name the worktree as `~/work`, and every other input as `~/attached/<its file or folder name>`. Attach only what its brief reads, never a whole run folder holding other worktrees.
3. Run `remote-agent --name <task> --attach <path>... @<message file> <worktree>` in the background, with a short task name the calling skill gives, or one you make from the task (lowercase, `[a-z0-9-]`). Its stderr is progress, one line per step, each starting with `[<task>]`. Its outcome is the run's `result.json` (step 4): read it there, never from its stdout. The report is the run's `report_path`: its full report, ending with its block. When the calling skill names a report file for it, copy `report_path` there.
   - Do not redirect its stderr: no `2>file`, no `2>&1 | <filter>`, no `2>/dev/null`. Left alone, each progress line shows to the human as it arrives; a run that only shows its result at the end looks like no agent is attached.
   - When the run starts, give the human the transcript path from its `transcript:` line on stderr. That file holds the subagent's full event stream as it arrives; beside it, `run.json` says the run's state and timings and `progress.log` holds its progress lines.
   - If your harness cannot show a background command's output while it runs, give the human the transcript path and read the run's stderr between other work.
   - The run keeps the remote subagent's session but does not place it for `/resume`: the sandbox wrote it, so it may hold forged turns that read as the human's. Never place it yourself. If the human wants to resume it, give them the `remote-agent resume <run> <worktree>` command from the run's `session:` line on stderr; it warns before placing.
4. When it ends, run `remote-agent result <task>` in the repo: it prints the newest `result.json` of the run named `<task>`, however the run was started (alone, beside others, or with its output redirected). Read `exit_code`, the report's `report_status` and `follow_up`, `changed` (the files it touched), and the full report at `report_path`. `complete: false` means the run ended before the subagent reported; anything unknown is `null`. The report's **block** is its last lines from the last `status:` line on (`status:`, `changed:`, `why:`, `follow-up:`); when the calling skill gives a report checker, check the block with it. Act on `exit_code`:
   - **0**: the changes are in the worktree, uncommitted. Commit them as the subagent would have, with the repo's commit convention, then act on the report.
   - **3**: no changes; act on the report.
   - **4**: the worktree changed after the snapshot; run it again.
   - **5**: the account hit its usage limit, so every remote run will fail the same way until it resets. Hold every new remote run, let the running ones end, and tell the human the reset time from stderr. The task that hit the limit waits with the held ones. Resume once that time has passed, or sooner when the human switches the account.
   - **Anything else**: treat it as `blocked`, with the last lines of stderr as the reason. When two runs in a row fail the same way within a minute, the backend is down: hold every new remote run until `remote-agent ready` exits 0 again.
