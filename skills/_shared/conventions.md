# Shared conventions

Steps several skills share. A skill links to the section it needs; follow that section as if the skill held it.

## Learn the rules

Read, if they exist: the skill's notes file (its rules override the skill's defaults), the repo's `AGENTS.md` (or `CLAUDE.md`), `CONTRIBUTING.md` and the PR template (`.github/pull_request_template.md`). Note the **identity** for GitHub and tracker calls (e.g. a `GH_TOKEN` prefix for a bot account); with none named, use the current login. If the rules name a setup that is missing, stop and ask the human. Then note what the skill's step lists.

## Subagents

Start the named agent if your harness defines it, otherwise a general-purpose subagent told to follow the brief. Hand over the inputs the brief lists as **paths**, never summaries, so its read is not anchored on yours. If your harness has no subagents, follow each brief yourself, one at a time, writing the same files.

At most **N** subagents run at once (the **slots**): `--parallel N`, else `max_parallel` from the notes file, else 4.
