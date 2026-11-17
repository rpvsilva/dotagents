<div align="center">

# 🤖 dotagents

**One AI setup, written once, shared by every coding tool.**

[![CI](https://github.com/rpvsilva/dotagents/actions/workflows/ci.yml/badge.svg)](https://github.com/rpvsilva/dotagents/actions/workflows/ci.yml)
![Claude Code](https://img.shields.io/badge/Claude_Code-supported-D97757?logo=anthropic&logoColor=white)
![Codex](https://img.shields.io/badge/Codex-supported-412991?logo=openai&logoColor=white)
![Cursor](https://img.shields.io/badge/Cursor-partial-000000?logo=cursor&logoColor=white)
![Shell](https://img.shields.io/badge/shell-bash_3.2+-4EAA25?logo=gnubash&logoColor=white)

🧩 14 skills · 🧑‍🤝‍🧑 9 subagents · 🪝 4 hooks · 🔌 2 MCP servers · ☁️ remote runs

[What's included](#-whats-included) · [Install](#-install) · [Use](#-use) · [Docs](#-docs) · [Contributing](#-contributing)

</div>

---

My personal AI setup, written once and shared by every AI coding tool I use: Claude Code, Codex and Cursor.

Each tool keeps its own instructions, skills, MCP servers and hooks in its own folder, in its own format. Keeping them in sync by hand means they drift, and switching tools or machines means starting over. This repo is the single source of truth. `install.sh` links it into each tool's config, so editing a file here changes every tool at once.

```mermaid
flowchart LR
  repo["dotagents repo<br/>skills, instructions,<br/>hooks, MCP servers"]
  install["install.sh<br/>(symlinks + merges)"]
  shared["~/.agents/<br/>skills, hooks, bin"]
  claude["~/.claude/"]
  codex["~/.codex/"]
  cursor["~/.cursor/"]
  repo --> install --> shared
  install --> claude
  install --> codex
  install --> cursor
  classDef src fill:#D97757,stroke:#B4532F,color:#fff
  classDef tool fill:#3B82F6,stroke:#1D4ED8,color:#fff
  classDef hub fill:#10B981,stroke:#047857,color:#fff
  class repo,install src
  class shared hub
  class claude,codex,cursor tool
```

> [!TIP]
> Terms like Setting, Lesson and Level have a precise meaning here: see [GLOSSARY.md](GLOSSARY.md).

## 📦 What's included

### 📝 Instructions

[`global/AGENTS.md`](global/AGENTS.md) holds my personal instructions for every project. It's linked as `~/.claude/CLAUDE.md` and `~/.codex/AGENTS.md`.

### 🧩 Skills

A **skill** is a `SKILL.md` file that teaches an agent one workflow. Skills are tool-neutral: each says *what* to do, never which tool does it. They live in [`skills/`](skills/). Steps several skills share are in [`skills/_shared/conventions.md`](skills/_shared/conventions.md).

#### 🔀 Pull requests

| Skill | What it does |
|---|---|
| [`address-review`](skills/address-review/SKILL.md) | Work through a PR's open review threads: fix what holds up, reply to the rest |
| [`fix-pr`](skills/fix-pr/SKILL.md) | Make one PR's failing checks pass and push |
| [`renovate-prs`](skills/renovate-prs/SKILL.md) | Fix, verify and merge open Renovate PRs, one subagent per PR |
| [`review-pr`](skills/review-pr/SKILL.md) | First-pass PR review by fresh subagents at the depth the change's risk tier needs, posted as a comment-only review |
| [`ship-pr`](skills/ship-pr/SKILL.md) | Take the current changes to a merged PR, by the repo's own rules |

#### 🗺️ Epics

| Skill | What it does |
|---|---|
| [`build-epic`](skills/build-epic/SKILL.md) | Build an epic from the tracker into one reviewed PR per ticket, using planner, explorer and implementer subagents |
| [`to-epic`](skills/to-epic/SKILL.md) | Turn meeting notes, docs or any source into an epic and its issues on the tracker |

#### 🩺 Code health

| Skill | What it does |
|---|---|
| [`security-audit`](skills/security-audit/SKILL.md) | Security-audit a repo of any size with subagents, then fix the findings you pick |
| [`tune-tests`](skills/tune-tests/SKILL.md) | Make the tests faster and their coverage wider in one PR |

#### ⚙️ The setup itself

| Skill | What it does |
|---|---|
| [`add-plugin`](skills/add-plugin/SKILL.md) | Add a Claude Code plugin after showing what it adds and costs |
| [`configure`](skills/configure/SKILL.md) | Show or change the Settings skills read, for this machine or this repo |
| [`remember`](skills/remember/SKILL.md) | After a skill run, save what it learned about the repo for next time |
| [`remote-subagents`](skills/remote-subagents/SKILL.md) | Run a subagent's brief on the remote backend (cluster, Docker or SSH host) instead of this machine, and act on its outcome |
| [`telemetry`](skills/telemetry/SKILL.md) | Show what a run cost per skill or per subagent role (`--by-role`), compare runs over time, find wasted steps, and measure and compare build-epic runs (`run_metrics.py`, `compare_runs.py`) |

### 🧑‍🤝‍🧑 Subagents

Some skills start **subagents**: focused helpers, each with a fresh context. Each one's instructions are a portable brief beside its skill. `adapters/<tool>/agents/` holds thin wrappers for Claude Code, Codex and Cursor that point at that brief.

| Subagent | Started by | Job |
|---|---|---|
| `epic-planner` | `build-epic` | Writes the spec, tickets and tasks |
| `epic-explorer` | `build-epic` | Explores one area of the repo and writes a cited note |
| `epic-implementer` | `build-epic` | Builds one task test-first in its own worktree |
| `source-reader` | `to-epic` | Reads one source and notes its needs, decisions and open questions |
| `pr-fixer` | `fix-pr`, `renovate-prs` | Takes one PR's failing checks to green |
| `pr-reviewer` | `review-pr`, `build-epic` | Finds bugs, rule breaks and missing tests in one PR |
| `security-mapper` | `security-audit` | Maps the attack surface into areas and flows |
| `security-reviewer` | `security-audit`, `review-pr` | Hunts security issues in one PR, area or flow |
| `test-tuner` | `tune-tests` | Makes tests faster and widens coverage |

Subagents can also run on a remote machine (a Kubernetes cluster, Docker or an SSH host) for compute only: the sandbox gets a snapshot of one worktree and sends back a patch, and never touches GitHub. Overview with diagrams: [docs/remote.md](docs/remote.md). Full reference: [remote/README.md](remote/README.md). The rules an agent follows to run one remotely live in the [`remote-subagents`](skills/remote-subagents/SKILL.md) skill.

### 🪝 Hooks

**Hooks** are scripts the tool runs before or after an action. Their logic lives in [`scripts/hooks/`](scripts/hooks/), and each tool's adapter wires them up. Details: [docs/hooks.md](docs/hooks.md).

| Hook | What it does | Tools |
|---|---|---|
| `guard-merge` | 🛑 Blocks `gh pr merge`, PR approvals and pushes to main in repos with a merge gate | Claude, Codex, Cursor |
| `guard-secrets` | 🔒 Blocks reading secret files and committing secrets | Claude, Codex, Cursor |
| `allow-task-cleanup` | ✅ Approves deleting finished branches and worktrees without asking | Claude |
| `telemetry-save` | 📊 Saves each session's cost and steps when it ends | Claude |

### 🔌 MCP servers

[`mcp/servers.json`](mcp/servers.json) is one list of MCP servers, installed into Claude Code, Codex and Cursor. Credentials are `${ENV_VAR}` references, with the values kept in `~/.config/dotagents/mcp.env`, which is never committed.

| Server | What it gives |
|---|---|
| `context7` | Up-to-date library and framework docs |
| `playwright` | A browser the agent can drive |

### ✨ Claude Code extras

- **Plugins** ([`adapters/claude/plugins.txt`](adapters/claude/plugins.txt)): `mattpocock-skills`, a set of engineering skills (grilling, TDD, code review, ...).
- **Mods** ([`adapters/claude/mods/`](adapters/claude/mods/)): `agent-flow` adds an `/agent-flow` pane drawing the session's agents as a live org chart, with a list of every agent and its log, remote-agent runs included (this session's under the agent that started them, other sessions' in the list).
- **Guardrails** ([`adapters/claude/settings/`](adapters/claude/settings/)): deny `gh pr review` and `gh auth token`, so an agent can't approve PRs or borrow my GitHub login. Codex gets the same rule in [`adapters/codex/rules/`](adapters/codex/rules/).

### 🧱 Groups

Every skill, subagent, mod, hook and folder part belongs to one **group**, a feature you can take on its own. Each part declares its group and the parts it needs in a `needs.json` beside it; the groups are named in [`groups.json`](groups.json), and `scripts/parts.sh` reads both.

| Group | What it holds |
|---|---|
| `core` | Always installed: Settings (`~/.agents/bin/settings`), the shared conventions, the hooks library and the `configure` skill |
| `remote` | Running subagents remotely: the `remote-agent` command and the `remote-subagents` skill; the `agent-flow` group shows the runs |
| `pr-loop` | `ship-pr`, `fix-pr`, `address-review`, `renovate-prs`, `review-pr` and their subagents |
| `epics` | `build-epic`, `to-epic`, `tune-tests` and their subagents |
| `security` | `security-audit` and its subagents |
| `safety-hooks` | The `guard-secrets`, `guard-merge` and `allow-task-cleanup` hooks |
| `agent-flow` | The `agent-flow` mod: the session's agents, remote runs included, as a live org chart, and each one's log |
| `small-tools` | `remember`, `telemetry`, the `telemetry-save` hook and the MCP servers |
| `personal` | My own instructions, guardrails and plugins (`global/`) and `add-plugin`; a public install leaves it out |

Install one group with `./install.sh <group>` (see [Install](#-install)).

## 🚀 Install

Needs `bash`, `git` and `jq`. The `claude` CLI is needed for plugins and MCP servers. `python3` (3.9 or later) is needed for `telemetry` and `build-epic` (its step scripts), and `gh` for the PR skills.

```sh
git clone git@github.com:rpvsilva/dotagents.git ~/Desktop/Projects/dotagents
~/Desktop/Projects/dotagents/install.sh          # everything, for every tool
```

`install.sh [tool] [<group>|<part>...]` and `uninstall.sh [--dry-run] [--purge] [tool] [<group>|<part>...]` take their words in any order: at most one tool word and any groups or parts (`scripts/parts.sh list`). `install.sh` takes the tool words `all`, `shared`, `claude`, `codex` and `cursor`; `uninstall.sh` takes `all`, `claude`, `codex` and `cursor`, not `shared` (the shared `~/.agents` links go only with `all`). A second tool word or an unknown word prints the usage and exits 1.

- A bare `./install.sh` installs every group, `personal` included. To install one feature only, name it: `./install.sh remote`, or for Claude Code only, `./install.sh claude pr-loop`.
- A selection brings core and every part it requires. An optional need comes only when you name it too.
- Installs add up: `./install.sh remote`, then `./install.sh pr-loop`, keeps remote and adds pr-loop.
- `./uninstall.sh <group>` removes that group, keeping core and anything another installed group still requires.

- Everything is symlinked, so editing the repo updates every tool at once.
- Re-run `install.sh` after adding a skill or moving the repo. Running it twice is safe.
- On a terminal, `install.sh` and `uninstall.sh` show a live view: a spinner and progress bar per tool, then a summary. `DOTAGENTS_PLAIN=1` prints every line instead.
- Anything it replaces is moved to `~/.dotagents-backup/` first.
- Codex and Cursor are skipped when `~/.codex` or `~/.cursor` doesn't exist.
- To remove it all, run `uninstall.sh` (`--dry-run` first to see the plan). See [Uninstall](docs/how-it-works.md#uninstall).

What gets linked where: [docs/how-it-works.md](docs/how-it-works.md).

## 🕹️ Use

Start any supported tool and call a skill by name, e.g. `/ship-pr` in Claude Code, or ask for it in plain words. Hooks and MCP servers work without any action from you.

To change how a skill behaves (how many subagents run at once, which tracker holds tickets), use the `configure` skill. See [docs/settings.md](docs/settings.md).

## 📚 Docs

| Page | Read it to learn |
|---|---|
| 🔧 [How it works](docs/how-it-works.md) | What `install.sh` links or merges where, for each tool, and how `uninstall.sh` undoes it |
| 🎛️ [Settings](docs/settings.md) | How to change a skill's behaviour, per machine or per repo |
| 🪝 [Hooks](docs/hooks.md) | What each hook blocks or approves, and why |
| ☁️ [Remote subagents](docs/remote.md) | Running subagents on a cluster, Docker or an SSH host, with diagrams |
| 💬 [Agent communication](docs/agent-communication.md) | Why subagents don't talk to each other |
| ⚖️ [Compared with Claude features](docs/claude-features.md) | How this setup differs from Claude Code's self-hosted environments and Claude Cowork |
| 🧪 [Evals](docs/evals.md) | Checking whether a skill change made it better or worse: the case format, running cases locally or on the remote backend, grading and comparing them, when they run, and the pilot |
| 👤 [Personal values](docs/personal.md) | Every value that is mine, and what a public install leaves out: my instructions, guardrails, plugins, gate, App identity and cluster |
| 📖 [Glossary](GLOSSARY.md) | The repo's terms |

## 🗂️ Layout

```
README.md           this file
AGENTS.md           how to work on this repo (CLAUDE.md links to it)
GLOSSARY.md         the repo's terms
groups.json         the groups every part belongs to
global/AGENTS.md    my global instructions, linked into every tool
skills/             tool-neutral skills; skills/_shared/ holds what they share
mcp/servers.json    MCP servers for every tool
scripts/hooks/      hook logic as plain scripts
scripts/            install helpers, settings, remote-agent, merge-pr, capped (runs a test under memory and time caps)
adapters/<tool>/    tool-only pieces: agent wrappers, hook wiring, rules
remote/             remote subagents
docs/               how it works, settings, hooks, remote subagents, agent communication, Claude features, evals
tests/              the checks CI runs (tests/run.sh); tests/capped.test.sh covers capped
evals/              eval cases per skill (evals/<skill>/<case>/) and their runner (evals/_runner/); dev tooling, never installed
```

## 🤝 Contributing

Read [AGENTS.md](AGENTS.md) first. It covers:
- the principles
- how to add a skill, subagent, tool, hook, plugin, mod or MCP server
- the checks (`tests/run.sh`)
- the PR rules: PRs merge only through `scripts/merge-pr.sh` and the `agent-pr-gate`
