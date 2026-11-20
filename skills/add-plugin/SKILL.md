---
name: add-plugin
description: Add a Claude Code plugin to this setup after Rui sees what it adds and costs; lists it in plugins.txt and ships the change.
disable-model-invocation: true
---

Add one Claude Code plugin to dotagents, so every machine that runs `install.sh claude` gets it. Claude Code only: other tools have no plugin list yet.

Input: a plugin name, a `<plugin>@<marketplace>` id, or a marketplace source (GitHub `owner/repo`, URL or path).

Work in the dotagents checkout this skill's folder lives in: `cd -P <this skill's folder>/../..`. Paths below are relative to it.

## Steps

1. **Check the list.** If `adapters/claude/plugins.txt` already names the plugin, say so and stop. Done when you know it is new.

2. **Find the id and source**: the `<plugin>@<marketplace>` id and the source `claude plugin marketplace add` takes.
   - Given a source: `claude plugin marketplace add <source>`, then read the plugin names in `~/.claude/plugins/marketplaces/<marketplace>/.claude-plugin/marketplace.json`. Several plugins: ask Rui which one.
   - Given a name: search the `marketplace.json` of each marketplace in `claude plugin marketplace list --json`; its `repo` is the source. No match: ask Rui for the source.

   Done when you have the id, the source, and whether this run added the marketplace.

3. **Install and inspect.** `claude plugin install <id> --scope user`, then `claude plugin details <id>`. Done when you have its component inventory and always-on token cost.

4. **Stop for approval.** Show Rui what the plugin is for, what it adds (skills, agents, hooks, MCP servers) and its always-on cost. Name each hook and MCP server: hooks run on every matching event, MCP servers run code on his machine. Ask whether to keep it. On drop: `claude plugin uninstall <id>`, `claude plugin marketplace remove <marketplace>` if step 2 added it, and stop. Done when Rui says keep or drop.

5. **List it.** Append `<id> <source>` to `adapters/claude/plugins.txt`, then run `./install.sh claude`. Done when its output shows `plugin <id>: already installed`.

6. **Ship it** with [ship-pr](../ship-pr/SKILL.md), commit `chore(claude): add <plugin> plugin`. In the PR body, name what the plugin adds and its always-on cost. `plugins.txt` is high tier, so the PR waits for Rui's approval. Done when ship-pr reports the PR merged or waiting.

## Report

The plugin id, what it adds, its always-on cost, and the PR URL with its state. Tell Rui the plugin loads from his next session.
