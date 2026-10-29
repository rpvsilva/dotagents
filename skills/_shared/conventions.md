# Shared conventions

Steps several skills share. A skill links to the section it needs; follow that section as if the skill held it.

## Learn the rules

Read, if they exist: the skill's notes file (its rules override the skill's defaults), the repo's `AGENTS.md` (or `CLAUDE.md`), `CONTRIBUTING.md` and the PR template (`.github/pull_request_template.md`). Note the **identity** for GitHub and tracker calls (e.g. a `GH_TOKEN` prefix for a bot account); with none named, use the current login. If the rules name a setup that is missing, stop and ask the human. Then note what the skill's step lists.

## Subagents

Start the named agent if your harness defines it, otherwise a general-purpose subagent told to follow the brief. Hand over the inputs the brief lists as **paths**, never summaries, so its read is not anchored on yours. If your harness has no subagents, follow each brief yourself, one at a time, writing the same files.

At most **N** subagents run at once (the **slots**): `--parallel N`, else `max_parallel` from the notes file, else 4.

## Remote subagents

A subagent the skill marks **remote-capable** (it only changes its worktree) runs on the remote backend set in `~/.config/dotagents/remote.env` (a cluster by default) instead of this machine when that file exists and the human did not ask for `--local`. The backend is compute only: it gets this machine's setup, a snapshot of the worktree and the files you attach, and gives back a patch. It has no GitHub access and no git branches, so it cannot push, open a PR, read CI logs or see another branch. Details: [remote/README.md](../../remote/README.md). The human creates `remote.env` with `~/.agents/bin/remote-agent setup` and proves it with `remote-agent check`; without it, subagents run locally.

1. Create the worktree as for a local subagent.
2. Write the message you would hand the subagent into a file. Name the worktree as `~/work`, and every other input as `~/attached/<its file or folder name>`. Attach only what its brief reads, never a whole run folder holding other worktrees.
3. Run `~/.agents/bin/remote-agent --name <task> --attach <path>... @<message file> <worktree>` in the background, with a short task name the skill gives (e.g. a build-epic task's `TT-NN-<slug>`). It takes a slot like a local subagent. Its stdout is the subagent's report; its stderr is progress, one line per step, each starting with `[<task>]`.
   - Follow each run's stderr as it arrives and relay a short line to the human per step (claimed, sent, each tool the subagent runs, the result). A run that only shows its result at the end looks like no agent is attached. If your harness cannot read a background command's output while it runs, say where it goes and check it between other work.
4. Act on the exit code:
   - **0**: the changes are in the worktree, uncommitted. Commit them as the subagent would have, with the repo's commit convention, then act on the report.
   - **3**: no changes; act on the report.
   - **4**: the worktree changed after the snapshot; run it again.
   - **Anything else**: treat it as `blocked`, with the last lines of stderr as the reason.
