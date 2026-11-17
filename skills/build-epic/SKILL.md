---
name: build-epic
description: Take an epic (or one issue) from the tracker to one reviewed PR per ticket, each built from tasks merged as commits by planner, explorer and implementer subagents, with one human approval of the plan.
disable-model-invocation: true
---

Build an epic as **tickets** made of **tasks**. A ticket is one tracker issue (or a few that share files): its own branch and one PR into the run's **base** (the default branch, or the integration branch `--into` names). A task is one implementer's slice of a ticket, merged into the ticket branch as one commit once the Checks pass; the ticket branch stays on this machine until the ticket ships, and its PR's one deep review covers every task. Tickets that share files run one after another in a **lane**; only lanes with disjoint write sets run in parallel.

Input: an epic or issue (link or ID, on any tracker), or plain text. Optional: `--parallel N`; `--explore` to always run the explorers; `--into <branch>` to build into an integration branch (it beats the `base` Setting). Where the tool has a launcher for this skill (Claude Code: `~/.agents/bin/build-epic <arguments>`, [docs](../../docs/build-epic-launcher.md)), start and resume runs with it: it loads only the tools a run calls.

You are the **orchestrator**. You run scripts, start subagents and talk to the human; you never read the code, the tracker or a subagent's work yourself, except to verify review findings.

## The loop

The run folder is `~/.agents/runs/<repo>/<epic-slug>/` (`<repo>` the repo folder's name, `<epic-slug>` the tracker ID plus a few words, `gh-12-due-dates`); `<run>` below. Every script is `python3 -I ~/.agents/skills/build-epic/scripts/<name>.py <run> ...`.

1. Run `next.py <run>`. Each line is one action, most urgent first: `<action> <id> [details]  (<step file>)`.
2. Do each action as its step file under [steps/](steps/) says. Read a step file the first time an action names it, and again after your context was compacted or cleared; never read the step files ahead of time.
3. Repeat from 1 once an action completes or a subagent returns. `wait` lines need nothing: a subagent is still running, or the ticket waits on a human or another PR.

Do every action `next.py` prints before running it again, starting subagents for all of them at once where the step says so. When `next.py` and a step file disagree, trust `next.py` and tell the human the line.

**After each shipped ticket**, the ship step runs `status.py <run> --write`. Then compact or clear your context if your harness can, keeping only the run folder's path and this file; otherwise act on `state.md` and `next.py` alone, not on what earlier turns said.

## Rules

- **Scripts own git, GitHub and the tracker.** Each step script prints one line, `OK <id> <state> [details]` or `FAIL <id> <reason> (log: <path>)`, and exits non-zero on `FAIL`. Act on that line; open the log only to understand a `FAIL` or to hand it to a subagent. Never compose the git or gh commands a script runs yourself: when a `FAIL` is one its step file does not cover, tell the human with the log path. Every gh call acts as the run's identity (`run.md`'s `Identity:` line); `FAIL <id> identity-token` means its token command failed, and `FAIL <id> identity-changed` that another session switched accounts: keep building, ask the human to switch back, then run `tracker_cmd.py <run> flush`.
- **Agents talk to machines.** Every prompt and output follows the [Protocol](../_shared/conventions.md#protocol): prompts are written by scripts, outputs land in `<run>/reports/<id>.json` (reviews in `<run>/reviews/`) and are checked by `report_check.py`. You act on the status alone.
- **You are the only one who talks to the human.** Everything you show them is rendered from the run folder: `view.py <run> plan|task <TT-NN>|report`, `status.py <run> --line <TT>`, and the `FAIL` line with its log path. Add your own judgement under it in a few sentences; never paste an agent's output or words. Tell them something only when a ticket ships, when `next.py` prints a `human` line, or when a step file says to.
- **The run's rules are decided once.** `decisions.md` is written only by `decide.py` and `setup.py`. Record every verbatim decision the human gives about the run's rules with `decide.py <run> quote "<their words>"`, and never ask again what `decisions.md` already answers. Never waive a rule yourself.
- **Status lines** (`Status:`, `PR:`) change only through the scripts, or where a step file tells you to set one.

## Subagents

| Agent | Brief | Role | Writes |
|---|---|---|---|
| `epic-planner` | [planner-brief.md](planner-brief.md) | `planner` | `spec.md`, `areas.md`, `map.md`, `tickets/`, `tasks/` |
| `epic-explorer` | [explorer-brief.md](explorer-brief.md) | `explorer` | `notes/<area>.md` |
| `epic-implementer` | [implementer-brief.md](implementer-brief.md) | `implementer` | commits on its task branch |
| `pr-reviewer`, `security-reviewer` | started by [review-pr](../review-pr/SKILL.md) | `reviewer`, `security` | their review output |

Start and limit them as [Subagents](../_shared/conventions.md#subagents) says: at most **N** at once (the slots, `decisions.md`'s `parallel:`). Each one's **description** (the label your harness saves with it) starts with its role, so telemetry files its cost by role (`ROLES` in `adapters/claude/telemetry/extract.py`): `plan: A`, `plan: B`, `plan: revise-<n>`, `explore: <area>`, `implement: TT-NN <title>`, `fix: TT-NN` (a fix task), `resolve: TT-NN`, `review: TT-NN` (a high-tier task), `ticket-review: PR <n>`, `re-review: PR <n>`, `security: PR <n>`.

An implementer or a reviewer runs remotely when [Remote subagents](../remote-subagents/SKILL.md) lets it (its declaration in `needs.json`, `remote-agent ready` exits 0, no `--local`); the steps say how. The planner and explorers write into the run folder, so they always run here.

## Run folder

`run.md` (repo, default branch, remote, identity, epic), `decisions.md` (the run's rules), `spec.md`, `areas.md`, `notes/`, `map.md` (its `Checks`, `Lanes` and `Gaps`), `tickets/TT-<slug>.md` and `tasks/TT-NN-<slug>.md` (their `Status:` lines: tickets `ready`, `claimed`, `building`, `in-review`, `merged`, `blocked`; tasks `ready`, `in-progress`, `done`, `blocked`), `plan/TT.md`, `prompts/`, `reports/` (agent outputs and `<id>.checks.json`), `reviews/`, `logs/`, `remote/`, `env/` (the one test environment), `skips.md`, `worktrees/`, `state.md` and `tracker.json`. Each step file says which of them it reads.

When the folder already exists, **resume**: run `resume.py <run>` (it settles what a stopped run left: merged PRs, finished and half-done tasks), then continue the loop.
