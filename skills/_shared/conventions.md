# Shared conventions

Steps several skills share. A skill links to the section it needs; follow that section as if the skill held it.

## Learn the rules

Run `~/.agents/bin/settings list <skill>` in the repo: the skill's **Settings**, one per line as `<skill>.<key> TAB <value> TAB <Level> TAB <meaning>`, already resolved across flag, repo, machine and default. Use them as they are; never read a settings file yourself. When it fails, relay its message and stop. Then read, if they exist: the skill's notes file (its Lessons inform your judgement), the repo's `AGENTS.md` (or `CLAUDE.md`), `CONTRIBUTING.md` and the PR template (`.github/pull_request_template.md`). A notes line stating a value the skill declares as a Setting is left over from before Settings existed: the Setting wins, and you tell the human that [configure](../configure/SKILL.md) can move the line. Note the **identity** for GitHub and tracker calls (e.g. a `GH_TOKEN` prefix for a bot account); with none named, use the current login. If the rules name a setup that is missing, stop and ask the human. Then note what the skill's step lists.

## Subagents

Start the named agent if your harness defines it, otherwise a general-purpose subagent told to follow the brief. Hand over the inputs the brief lists as **paths**, never summaries, so its read is not anchored on yours: the prompt is the brief's path, then one `<input>: <path>` line per input (the task, the run folder, the worktree, the report file), and never restates what those files hold. If your harness has no subagents, follow each brief yourself, one at a time, writing the same files.

At most **N** subagents run at once on this machine (the **slots**): `--parallel N`, else the skill's `max_parallel` Setting; when no Level sets it, the run may compute the default from the machine, as the skill says. [Remote subagents](../remote-subagents/SKILL.md) take **remote slots** instead, counted apart: at most `~/.agents/bin/settings get remote max_sandboxes` at once, whatever the skill. When a skill says "while a slot is free", it means a slot of the kind the subagent will use, so a run with remote on can have N local and the remote slots' worth of subagents going at once. Slots are a **ceiling**, never a target: start a subagent when its work is ready and separable from what is running. Every run spends the account's usage quota, so the work sizes a burst.

Run your own git commands on a shared repo one at a time (`fetch`, `worktree add` and `remove`, `branch -D`, `push`): git locks its refs and config, so two at once fail.

### Reports

When the prompt names a **report file** (a skill with a run folder names `<run>/reports/<id>.md`), the subagent first writes its full report there, ending with its block, and then its final message is only the **block**: the lines `status:`, `changed:`, `why:` and `follow-up:`, in that order (empty values allowed), at most 400 characters, optionally inside one code fence. A longer or other-shaped message is a brief violation. With no report file named, the final message is the full report, ending with the block. Whoever started it reads the block alone and opens the report file only on `blocked` or `plan-wrong`, or when the skill's next step reads it (a review's findings, a tuner's mutations). Check each block with the calling skill's report checker, when it has one: a brief violation it reports means the subagent broke its brief; continue it once (or start a new one on the same worktree) asking for the report file and the block, and on a second violation act as on `blocked`, with the violation as the `why`. A remote subagent cannot reach the run folder: see [Remote subagents](../remote-subagents/SKILL.md) step 3.
