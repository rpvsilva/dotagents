# Shared conventions

Steps several skills share. A skill links to the section it needs; follow that section as if the skill held it.

## Paths and core commands

A path a skill or brief names is relative to the folder holding the file that names it (`scripts/x.py`, `../_shared/protocol/check.py`); run it by its full path. All installed skills sit side by side, so `../<skill>/` reaches another skill. The core commands `settings` and `capped` sit in `~/.agents/bin/` after `./install.sh` or the core bootstrap `./install.sh core`; call them by name, or by that path when `~/.agents/bin` is not on `PATH`.

## Learn the rules

Run the `settings` core command, `settings list <skill>`, in the repo: the skill's **Settings**, one per line as `<skill>.<key> TAB <value> TAB <Level> TAB <meaning>`, already resolved across flag, repo, machine and default. Use them as they are; never read a settings file yourself. When it fails, relay its message and stop. Then read, if they exist: the skill's notes file (its Lessons inform your judgement), the repo's `AGENTS.md` (or `CLAUDE.md`), `CONTRIBUTING.md` and the PR template (`.github/pull_request_template.md`). A notes line stating a value the skill declares as a Setting is left over from before Settings existed: the Setting wins, and you tell the human that [configure](../configure/SKILL.md) can move the line. Note the **identity** for GitHub and tracker calls (e.g. a `GH_TOKEN` prefix for a bot account); with none named, use the current login. If the rules name a setup that is missing, stop and ask the human. Then note what the skill's step lists.

## Subagents

Start the named agent if your harness defines it, otherwise a general-purpose subagent told to follow the brief. Hand over the inputs the brief lists as **paths**, never summaries, so its read is not anchored on yours: the prompt is the brief's path, then one `<input>: <path>` line per input (the task, the run folder, the worktree, the report file), and never restates what those files hold. If your harness has no subagents, follow each brief yourself, one at a time, writing the same files.

At most **N** subagents run at once on this machine (the **slots**): `--parallel N`, else the skill's `max_parallel` Setting; when no Level sets it, the run may compute the default from the machine, as the skill says. [Remote subagents](../remote-subagents/SKILL.md) take **remote slots** instead, counted apart: at most `settings get remote max_sandboxes` at once, whatever the skill. When a skill says "while a slot is free", it means a slot of the kind the subagent will use, so a run with remote on can have N local and the remote slots' worth of subagents going at once. Slots are a **ceiling**, never a target: start a subagent when its work is ready and separable from what is running. Every run spends the account's usage quota, so the work sizes a burst.

Run your own git commands on a shared repo one at a time (`fetch`, `worktree add` and `remove`, `branch -D`, `push`): git locks its refs and config, so two at once fail.

### Protocol

Agents talk to machines: every prompt and every output is JSON, one schema per **role** in [protocol/schemas/](protocol/schemas/) (its `input` and its `output`), and nothing an agent writes is meant for a person. Each brief names its role.

- **Prompt**: written by [protocol/prompt.py](protocol/prompt.py) (`prompt.py <role> key=text key:=json ... --out <file>`), or by the calling skill's own prompt script, never by hand: the line naming the agent and its brief, then the role's input object. Inputs are paths, never what the files hold.
- **Output**: when the inputs name an `output` file (a skill with a run folder names `<run>/reports/<id>.json`), the agent writes its output object there, then its final message is only `{"status": "<its status>", "output": "<that path>"}`, at most 400 characters, optionally inside one code fence. With no `output` named (a remote run, or a skill without a run folder), its final message is the output object itself.
- **Check** every output with [protocol/check.py](protocol/check.py) (`check.py <role> output <file> --message -`, the final message on stdin), or the calling skill's checker built on it. `OK <role> <status>`: act on the status alone, and open the output only on a status other than `done` or where the skill's next step reads it. `FAIL <role> brief-violation <problems>`: the agent broke its brief; continue it once (or start a new one on the same worktree) naming the problems, and on a second violation act as on `blocked`, with the violation as the `why`.
- **Render**: a person reads agent output only through [protocol/render.py](protocol/render.py) (`render.py <role> <file>`) or a skill script built on it, never the JSON or the agent's words passed on by hand. A remote subagent cannot reach the run folder: see [Remote subagents](../remote-subagents/SKILL.md) step 3.
