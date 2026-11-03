---
name: configure
description: Show or change the Settings skills and scripts read (how many subagents run at once, the tracker, labels, where remote subagents run, ...), for one run, this repo or this machine. Use when the human asks to configure a skill, turns a behaviour on or off, even mid-run ("disable remote", "use 2 agents in this repo"), or says what a skill should do from now on.
---

Show and change **Settings**: named values with a default that skills and scripts act on. Each skill declares the Settings it reads, with their kind, default and meaning; a key no skill declares does not exist. Settings are not **Lessons** (free text in a repo's notes files, owned by [remember](../remember/SKILL.md)) and never **Secrets** (tokens and other credentials).

Every read and write goes through `~/.agents/bin/settings`; never edit a settings file yourself. It resolves each value by **Level**, strongest first:

- **flag**: one run (`--parallel N`, `--local`, or a remote Setting's `REMOTE_*` variable)
- **repo**: `.agents/settings.json`, committed, for everyone working in the repo
- **machine**: `~/.config/dotagents/settings.json`, for this person across all repos
- **default**: the skill's own

Remote Settings (section `remote`) live at the machine Level only, except `remote.allowed`: a repo can set it to `false` to keep its worktree snapshots on this machine.

Input: nothing, to show the Settings; or a change, exact (`<section> <key> <value>`, with `--machine` or `--repo`) or in plain words. A section is a skill's name, `remote`, or `shared` for the keys several skills read (`max_parallel`, `tracker`).

## Steps

1. **Show.** When a change is asked, run `~/.agents/bin/settings list <section>` for the sections it touches and `~/.agents/bin/settings check`, show the keys it touches and every problem, and go to step 2. Otherwise, in the target repo, run `~/.agents/bin/settings list` (or `list <section>` when the human named a skill) and `~/.agents/bin/settings check`. Each `list` line is `<section>.<key> TAB <value> TAB <Level> TAB <meaning>`. Group the lines by section, as a table of key, value, Level and meaning; list every problem `check` reports. Then read the repo's notes files (`.agents/*-notes.md`) for lines that state a value a skill declares as a Setting (e.g. `max_parallel: 2` in `.agents/build-epic-notes.md`); they are left over from before Settings existed, and the skills no longer read them. Done when the human has seen every Setting, each problem, and each leftover notes line. With no change asked, stop here.

2. **Resolve the change** into one or more `<section> <key> <value>` triples, each with a Level:
   - **flag** when the human means one run ("for this epic", "just now"): name the flag (`--parallel N`, `--local`, or the Setting's `REMOTE_*` variable) for the running skill to use, and stop; nothing is written.
   - **repo** when the value describes the repo (its tracker, its labels) or the human says "here" or "in this repo"; **machine** when it is personal or a remote Setting. When it is unclear, ask.
   - Remote off has three scopes: `--local` (one run), `remote.allowed false` (this repo, repo Level), or `remote.backend` unset (every repo, machine Level). A bare "disable remote" names none of them: ask which.
   - A key no skill declares: say so, name the declared keys that come closest, and ask. Never invent a key.
   - A Secret (the Claude token, MCP credentials, the GitHub App's credentials): never ask for it or read its file. Point to the command that owns it: `~/.agents/bin/remote-agent setup` for the token, `~/.config/dotagents/mcp.env` and `~/.config/dotagents/github-app.env` (see the dotagents README) for the rest. The human edits those themselves.
   - Remote subagents set up from scratch: suggest the human run `~/.agents/bin/remote-agent setup` in their terminal instead (it asks for the token), then `remote-agent check`.
   - Each leftover notes line from step 1 that the human wants kept becomes a repo-Level change, plus removing that line from the notes file.

   Done when every change has a declared key, a value and a Level.

3. **Confirm.** Show each change as `<section>.<key>: <old value> (<old Level>) → <new value> (<Level>)`, and each notes line to remove. Wait for the human's yes; on an edit, redo step 2. Done when they approve, or drop it.

4. **Apply.** Run `~/.agents/bin/settings set <section> <key> <value> --machine|--repo` for each change (`unset` to go back to the default), then remove the notes lines. When `set` refuses a value, relay its message and go back to step 2. Then run `~/.agents/bin/settings check`; after a remote change, suggest `~/.agents/bin/remote-agent check`. Done when `check` prints `ok`.

5. **Ship.** A repo-Level change (`.agents/settings.json`, and any notes file edited) goes up as one PR by the [ship-pr](../ship-pr/SKILL.md) steps, unless the human asks to leave it uncommitted. A machine-Level change needs nothing more. Done when the PR URL exists, or the human chose otherwise.

## Report

Each change as `<section>.<key>: <old> → <new> (<Level>)`, the notes lines moved, and the PR URL.
