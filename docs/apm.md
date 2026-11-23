# 🧭 APM study

[← Back to README](../README.md)

This page records whether [APM](https://github.com/microsoft/apm) (the Agent Package Manager) can replace `install.sh` as the way a stranger installs a Group. The study installed every public Group (all of `groups.json` but `personal`) with APM 0.36.1 at user scope, for Claude Code, Codex and Cursor, on 2026-11-20. The raw matrix is [`tools/apm-study/results.json`](../tools/apm-study/results.json); the tooling is in [`tools/apm-study/`](../tools/apm-study/) (dev tooling like `evals/`, run on the maintainer's machine only).

## Decision

**Not now.** `install.sh` stays the install path; the README quick start is unchanged.

Reasons:
- No Group installs fully in any tool: every cell is `fails` or `partial`.
- Hooks, the `agent-flow` mod, the `remote` command and MCP servers have no APM primitive, so three Groups install nothing of what makes them useful.
- Every skill needs the shared files and the core commands (`settings`, `capped`). APM ships neither: a skill installed by APM finds `../_shared/` nowhere, and an APM install gets the core commands only after `./install.sh core`, the core bootstrap.
- The packages needed workarounds (absolute paths, a generated `_shared` package, one package per agent and tool) that this repo would have to maintain beside `install.sh`.

What would change it: APM gains primitives for hooks, commands on PATH, support files and plugins (the APM issues below), and a rerun of `tools/apm-study/run.sh` turns the `fails` cells into `ok`. Then the choice is adopt alongside `install.sh` first. If it is adopt, a follow-up epic issue in this repo, opened by the maintainer's run identity, is named here.

Study version: APM 0.36.1, 2026-11-20.

## Matrix

Group by tool. `fails` means nothing useful landed; `partial` means the skills landed and something the Group needs did not.

| Group | Claude Code | Codex | Cursor |
|---|---|---|---|
| `remote` | fails | fails | fails |
| `safety-hooks` | fails | fails | fails |
| `agent-flow` | fails | fails | fails |
| `small-tools` | partial | partial | partial |
| `pr-loop` | partial | partial | partial |
| `epics` | partial | partial | partial |
| `security` | partial | partial | partial |

## Breakages

Each breakage found, with who must fix it: this repo, or APM.

| Id | What breaks | Owner | Why |
|---|---|---|---|
| `relative-path-deps` | A package's `../x` path dependencies fail to resolve: APM copies the package first, so the meta-package installs nothing until the paths are absolute. | APM | A meta-package cannot depend on a sibling folder by relative path. |
| `shared-missing` | The shared files package has no SKILL.md, so APM deploys nothing: `../_shared/` links and protocol scripts of every skill point nowhere. | APM | A package with files but no primitive (no `SKILL.md`) deploys nothing, so support files such as `_shared` cannot be shipped. |
| `core-commands-missing` | No `settings` or `capped` command lands on PATH or in `~/.agents/bin`: skills that call them need `./install.sh core` as well. | this repo | APM installs no command on PATH; the core bootstrap (`./install.sh core`) is the answer and the page says so. |
| `command-not-installed` | The remote-agent command and its scripts are not installed: the remote-subagents skill has nothing to call. | APM | No primitive for commands on PATH. |
| `hooks-not-installed` | No hook script, wiring or hooks library lands (the generator has no hook package): the Group's hooks never run. | APM | No hook primitive: scripts, wiring and the hooks library cannot be installed. |
| `mod-not-installed` | The agent-flow mod is a Claude Code plugin: nothing lands, only the core skill. | APM | No primitive for a Claude Code plugin. |
| `mcp-not-installed` | The MCP servers are not installed: no ${ENV_VAR} filling from `mcp/servers.json`. | APM | No way to fill `${ENV_VAR}` credentials into `mcp/servers.json`. |
| `codex-toml-skipped` | Codex `.toml` wrappers are skipped as non-agent assets; Codex gets the Claude `.md` compiled to `.toml` instead. | APM | Codex `.toml` agents are skipped as non-agent assets. |
| `codex-agent-lossy` | Compiled Codex agents keep a Claude model alias, drop `isolation` and `disallowedTools`, and name the brief under `~/.claude/skills`. | APM | The Claude-to-Codex agent compile keeps a Claude model alias and drops `isolation` and `disallowedTools`; the brief path is this repo's wrapper text. |
| `agent-name-collision` | The claude, codex and cursor wrapper packages of an agent deploy the same file name: the first (Claude's) wins and APM skips the others as local files. | this repo | One agent name ships once per tool, so the three wrapper packages collide; the packages would need one file per name and tool folder. |
| `cursor-agent-is-claude` | Cursor agents are Claude's wrappers (`model: sonnet`, `isolation`, brief under `~/.claude/skills`), not the Cursor wrappers. | APM | Cursor agents are compiled from the Claude wrappers, not the Cursor ones. |

An APM install gets the core commands and the shared files through the core bootstrap, `./install.sh core`, which links `_shared` beside the skills and puts `settings` and `capped` in `~/.agents/bin`. APM alone cannot give a skill either.

## Draft upstream issues

One draft per APM-owned breakage, for the maintainer to file on microsoft/apm. Replace the `Status` line by the issue link once filed.

### `relative-path-deps`

Status: not filed.

**Title:** Meta-packages cannot depend on sibling packages by relative path

**Body:**

> Trying to install dotagents (an agent setup of skills, agents, hooks, MCP servers and mods) with APM 0.36.1 at user scope for Claude Code, Codex and Cursor.
>
> What happens: A package's `../x` path dependencies fail to resolve: APM copies the package first, so the meta-package installs nothing until the paths are absolute.
>
> What is expected: A meta-package cannot depend on a sibling folder by relative path; or a documented way to express it.
>
> Reproduce: `tools/apm-study/run.sh <group> <claude|codex|cursor>` in the dotagents repo, in a throwaway `$HOME`. Affected cells: agent-flow/claude, agent-flow/codex, agent-flow/cursor, epics/claude and more.

### `shared-missing`

Status: not filed.

**Title:** A package without a SKILL.md deploys nothing (support files cannot ship)

**Body:**

> Trying to install dotagents (an agent setup of skills, agents, hooks, MCP servers and mods) with APM 0.36.1 at user scope for Claude Code, Codex and Cursor.
>
> What happens: The shared files package has no SKILL.md, so APM deploys nothing: `../_shared/` links and protocol scripts of every skill point nowhere.
>
> What is expected: A package with files but no primitive (no `SKILL.md`) deploys nothing, so support files such as `_shared` cannot be shipped; or a documented way to express it.
>
> Reproduce: `tools/apm-study/run.sh <group> <claude|codex|cursor>` in the dotagents repo, in a throwaway `$HOME`. Affected cells: agent-flow/claude, agent-flow/codex, agent-flow/cursor, epics/claude and more.

### `command-not-installed`

Status: not filed.

**Title:** No way to install a command on PATH

**Body:**

> Trying to install dotagents (an agent setup of skills, agents, hooks, MCP servers and mods) with APM 0.36.1 at user scope for Claude Code, Codex and Cursor.
>
> What happens: The remote-agent command and its scripts are not installed: the remote-subagents skill has nothing to call.
>
> What is expected: No primitive for commands on PATH; or a documented way to express it.
>
> Reproduce: `tools/apm-study/run.sh <group> <claude|codex|cursor>` in the dotagents repo, in a throwaway `$HOME`. Affected cells: remote/claude, remote/codex, remote/cursor.

### `hooks-not-installed`

Status: not filed.

**Title:** No hook primitive

**Body:**

> Trying to install dotagents (an agent setup of skills, agents, hooks, MCP servers and mods) with APM 0.36.1 at user scope for Claude Code, Codex and Cursor.
>
> What happens: No hook script, wiring or hooks library lands (the generator has no hook package): the Group's hooks never run.
>
> What is expected: No hook primitive: scripts, wiring and the hooks library cannot be installed; or a documented way to express it.
>
> Reproduce: `tools/apm-study/run.sh <group> <claude|codex|cursor>` in the dotagents repo, in a throwaway `$HOME`. Affected cells: safety-hooks/claude, safety-hooks/codex, safety-hooks/cursor, small-tools/claude and more.

### `mod-not-installed`

Status: not filed.

**Title:** No primitive for a Claude Code plugin

**Body:**

> Trying to install dotagents (an agent setup of skills, agents, hooks, MCP servers and mods) with APM 0.36.1 at user scope for Claude Code, Codex and Cursor.
>
> What happens: The agent-flow mod is a Claude Code plugin: nothing lands, only the core skill.
>
> What is expected: No primitive for a Claude Code plugin; or a documented way to express it.
>
> Reproduce: `tools/apm-study/run.sh <group> <claude|codex|cursor>` in the dotagents repo, in a throwaway `$HOME`. Affected cells: agent-flow/claude, agent-flow/codex, agent-flow/cursor.

### `mcp-not-installed`

Status: not filed.

**Title:** No way to fill ${ENV_VAR} credentials into MCP servers

**Body:**

> Trying to install dotagents (an agent setup of skills, agents, hooks, MCP servers and mods) with APM 0.36.1 at user scope for Claude Code, Codex and Cursor.
>
> What happens: The MCP servers are not installed: no ${ENV_VAR} filling from `mcp/servers.json`.
>
> What is expected: No way to fill `${ENV_VAR}` credentials into `mcp/servers.json`; or a documented way to express it.
>
> Reproduce: `tools/apm-study/run.sh <group> <claude|codex|cursor>` in the dotagents repo, in a throwaway `$HOME`. Affected cells: small-tools/claude, small-tools/codex, small-tools/cursor.

### `codex-toml-skipped`

Status: not filed.

**Title:** Codex .toml agents are skipped as non-agent assets

**Body:**

> Trying to install dotagents (an agent setup of skills, agents, hooks, MCP servers and mods) with APM 0.36.1 at user scope for Claude Code, Codex and Cursor.
>
> What happens: Codex `.toml` wrappers are skipped as non-agent assets; Codex gets the Claude `.md` compiled to `.toml` instead.
>
> What is expected: Codex `.toml` agents are skipped as non-agent assets; or a documented way to express it.
>
> Reproduce: `tools/apm-study/run.sh <group> <claude|codex|cursor>` in the dotagents repo, in a throwaway `$HOME`. Affected cells: epics/codex, pr-loop/codex, security/codex.

### `codex-agent-lossy`

Status: not filed.

**Title:** Compiled Codex agents lose fields

**Body:**

> Trying to install dotagents (an agent setup of skills, agents, hooks, MCP servers and mods) with APM 0.36.1 at user scope for Claude Code, Codex and Cursor.
>
> What happens: Compiled Codex agents keep a Claude model alias, drop `isolation` and `disallowedTools`, and name the brief under `~/.claude/skills`.
>
> What is expected: The Claude-to-Codex agent compile keeps a Claude model alias and drops `isolation` and `disallowedTools`; the brief path is this repo's wrapper text; or a documented way to express it.
>
> Reproduce: `tools/apm-study/run.sh <group> <claude|codex|cursor>` in the dotagents repo, in a throwaway `$HOME`. Affected cells: epics/codex, pr-loop/codex, security/codex.

### `cursor-agent-is-claude`

Status: not filed.

**Title:** Cursor agents are compiled from the Claude wrappers

**Body:**

> Trying to install dotagents (an agent setup of skills, agents, hooks, MCP servers and mods) with APM 0.36.1 at user scope for Claude Code, Codex and Cursor.
>
> What happens: Cursor agents are Claude's wrappers (`model: sonnet`, `isolation`, brief under `~/.claude/skills`), not the Cursor wrappers.
>
> What is expected: Cursor agents are compiled from the Claude wrappers, not the Cursor ones; or a documented way to express it.
>
> Reproduce: `tools/apm-study/run.sh <group> <claude|codex|cursor>` in the dotagents repo, in a throwaway `$HOME`. Affected cells: epics/cursor, pr-loop/cursor, security/cursor.
