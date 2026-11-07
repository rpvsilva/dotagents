# 🎛️ Settings

[← Back to README](../README.md)

A **Setting** is a named value with a default that a skill reads and acts on. Examples: how many subagents run at once, or which tracker holds tickets. It's the way to change a skill's behaviour without editing the skill.

## Where they're declared

Each skill lists the Settings it reads, called its **Declaration**, in `skills/<skill>/settings.json`. Each key has a kind, a default and a meaning:

```json
{
  "auto_save": { "kind": "bool", "default": true, "meaning": "Save each Claude Code session's run as JSON in ~/.agents/telemetry/ when the session ends." }
}
```

- Kinds are `int` (with optional `min`/`max`), `bool`, `string`, `enum` (with `choices`) and `list`.
- Keys several skills read live once in [`skills/_shared/settings.json`](../skills/_shared/settings.json) (`max_parallel`, `tracker`). A skill opts in with `{"shared": true}` and never redeclares the key.
- The remote backend's Settings are in [`remote/settings.json`](../remote/settings.json).

A Setting that no skill declares doesn't exist.

## Where values live (Levels)

A value can come from four **Levels**. Strongest first:

| Level | Where | Applies to |
|---|---|---|
| flag | the key's `env` variable, or e.g. `--parallel N` | one run |
| repo | `.agents/settings.json` at the repo root, committed | everyone working in that repo |
| machine | `~/.config/dotagents/settings.json` | you, across all repos |
| default | the Declaration | everyone |

Within a Level, a skill's own entry beats a shared one:

```json
{ "max_parallel": 4, "build-epic": { "max_parallel": 2 } }
```

Here `build-epic` runs 2 subagents at once and every other skill runs 4.

This repo sets two at the repo Level, in its own [`.agents/settings.json`](../.agents/settings.json): `build-epic`'s `merge_command` (`scripts/merge-pr.sh {pr}`, so task and ticket PRs merge through the gate) and `identity_command` (`scripts/gh-app-token.sh`, so every `gh` call acts as the App).

build-epic's `task_merge_method` set to `merge` needs a `merge_command` that is `merge-pr.sh` (it passes `--merge`); with any other merge rule, a task merge fails `merge-method` instead of squashing.

## Reading and changing them

Use the `configure` skill and just say what you want: "use 2 agents in this repo", or "disable remote". It shows the change, and a repo-level change ships as a PR.

Or use the command directly:

```sh
~/.agents/bin/settings list                          # every Setting, its value and Level
~/.agents/bin/settings get build-epic max_parallel
~/.agents/bin/settings set shared max_parallel 2 --machine
~/.agents/bin/settings unset build-epic max_parallel --repo
~/.agents/bin/settings check                         # validate both files
```

A bad value fails loudly and names the file and key. It never falls back to the default silently. Full usage is at the top of [`scripts/settings.sh`](../scripts/settings.sh).

## What's not a Setting

- **Secrets** (tokens, keys) are never Settings. They stay in `~/.config/dotagents/*.env`, each written by its own command.
- **Lessons** (what a skill had to learn about one repo the hard way) go in that repo's `.agents/<skill>-notes.md`, written by the `remember` skill.
