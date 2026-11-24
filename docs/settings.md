# 🎛️ Settings

[← Back to the docs](README.md)

> **TL;DR** Want a skill to behave differently? Don't edit the skill. Tell the `configure` skill what you want ("use 2 agents in this repo") and it changes the right Setting.

A **Setting** is a named value, with a default, that a skill reads and acts on. How many subagents run at once? Which tracker holds tickets? Those are Settings.

## 🗣️ The easy way: just ask

Use the `configure` skill and say it in plain words:

- "use 2 agents in this repo"
- "disable remote"

It shows you the change before making it. A repo-level change ships as a PR.

## ⌨️ The direct way

```sh
~/.agents/bin/settings list                          # every installed part's Settings, values and Levels
~/.agents/bin/settings get build-epic max_parallel
~/.agents/bin/settings set shared max_parallel 2 --machine
~/.agents/bin/settings unset build-epic max_parallel --repo
~/.agents/bin/settings check                         # validate both files
```

- `list` shows only installed parts: `shared`, each skill linked as `~/.agents/skills/<skill>`, and `remote` when `~/.agents/bin/remote-agent` is linked. Before any install (no `~/.agents/skills` yet), it shows every part.
- A value saved for a part that isn't installed is kept. `get`, `set`, `unset` and `check` still accept it. It just isn't listed.
- **A bad value fails loudly**, naming the file and key. It never quietly falls back to the default.

Full usage is at the top of [`scripts/settings.sh`](../scripts/settings.sh).

<a id="where-values-live-levels"></a>

## 🪜 Where values live (Levels)

A value can come from four **Levels**. The strongest wins:

| Level | Where | Applies to |
|---|---|---|
| 💪 flag | the key's `env` variable, or a flag like `--parallel N` | one run |
| 📁 repo | `.agents/settings.json` at the repo root, committed | everyone working in that repo |
| 💻 machine | `~/.config/dotagents/settings.json` | you, in every repo |
| 📄 default | the Declaration | everyone |

Within one Level, a skill's own entry beats a shared one:

```json
{ "max_parallel": 4, "build-epic": { "max_parallel": 2 } }
```

Here `build-epic` runs 2 subagents at once, and every other skill runs 4.

> 💡 Real example: this repo sets two values at the repo Level, in its own [`.agents/settings.json`](../.agents/settings.json). `build-epic`'s `merge_command` is `scripts/merge-pr.sh {pr}`, so ticket PRs merge through the gate. Its `identity_command` is `scripts/gh-app-token.sh`, so every `gh` call acts as the App.

## 📜 Where they're declared

Each skill lists the Settings it reads in `skills/<skill>/settings.json`, called its **Declaration**. Each key has a kind, a default and a meaning:

```json
{
  "auto_save": { "kind": "bool", "default": true, "meaning": "Save each Claude Code session's run as JSON in ~/.agents/telemetry/ when the session ends." }
}
```

- **Kinds:** `int` (with optional `min`/`max`), `bool`, `string`, `enum` (with `choices`) and `list`.
- **Shared keys** that several skills read (`max_parallel`, `tracker`) live once in [`skills/_shared/settings.json`](../skills/_shared/settings.json). A skill opts in with `{"shared": true}` and never redeclares the key.
- **The remote backend's Settings** are in [`remote/settings.json`](../remote/settings.json).

If no skill declares a Setting, it doesn't exist.

## 🚫 What's not a Setting

- **Secrets** (tokens, keys) are never Settings. They stay in `~/.config/dotagents/*.env`, each written by its own command.
- **Lessons** (what a skill had to learn the hard way about one repo) go in that repo's `.agents/<skill>-notes.md`, written by the `remember` skill.
