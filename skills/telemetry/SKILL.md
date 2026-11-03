---
name: telemetry
description: Show what the run that just finished cost, split by skill: wall clock, turns, tool calls, tokens, cost; optionally save it as JSON. Or compare a skill's saved runs over time, or analyse its steps across them and suggest changes to the skill.
disable-model-invocation: true
---

Report what the **run** cost: this session from its start (or from the previous telemetry report) up to this request, subagents included. The report splits the run into **spans**, one per skill the user typed; skills those load count toward them, and activity before any skill is the `no skill` span. Scripts compute every number; you relay them and read them.

Claude Code also saves every session's whole run on its own when the session ends, as `~/.agents/telemetry/<session id>.json` (a session that ends again overwrites its file; its `date` is the UTC date of its start), through the session-end hook `scripts/hooks/telemetry-save.sh`. The Setting `auto_save` turns that off: `~/.agents/bin/settings set telemetry auto_save false --machine` (or `--repo`), or the `configure` skill. The `--save` below is separate and unchanged.

Arguments, all optional: a **skill** name to report only that skill's last span; `--save <label>`; `--session <id>` to report on an older session instead of this one.

Or `compare [skill] [--since YYYY-MM-DD] [--until YYYY-MM-DD]`: compare saved runs over time instead of reporting this one; go to step 1, then straight to step 6.

Or `analyse <skill> [--top N]`: find the slowest, failing, repeated and retried steps of a skill across its saved runs, and suggest what to change in it; go to step 1, then straight to step 7.

## Steps

1. **Find the scripts.** Resolve this skill folder's real path (`cd <skill folder> && pwd -P`): it sits in the dotagents repo at `skills/telemetry/`. Beside it are `report.py`, `compare.py` and `analyse.py`; the extractor for the tool you are running in is `adapters/<tool>/telemetry/extract.py` from the repo root (`claude` for Claude Code). If your tool has no extractor, say so and stop (for `compare`, only `compare.py` is needed; for `analyse`, only `analyse.py`). Done when the paths you need exist.

2. **Extract the run.** `python3 <extract.py> [--skill <name>] [--session <id>] > <temp>/run.json`. Exit code 2 means there is nothing to measure (the skill never ran, or nothing happened since the last report): relay its message and stop. Done when `run.json` holds the run.

3. **Report.** `python3 <report.py> <temp>/run.json [--save <label>]`. Show the output verbatim in a code block, then three plain lines: the costliest span and what drove it (output, cache writes or cache reads), the busiest tool or subagent, and the cheapest lever to try next. Done when the table and the three lines are shown.

4. **Check prices**, only when the report ends with a `check with: update_pricing.py …` line (prices over 30 days old, or a model with no price). Run that command from the skill folder: it compares `pricing.json` with LiteLLM's price list and writes nothing. Show its changes and ask whether to apply them; on yes, re-run it with `--apply`, then re-run step 3. If it prints `LiteLLM unavailable`, say prices stay as they are and go on. Done when prices are applied, declined, or unavailable.

5. **Offer to save**, unless `--save` was already given: ask once whether to save this run as JSON (to `.agents/telemetry/` at the repo root), and for a label. Saved runs hold step labels (masked commands and paths), so the first save writes a `.gitignore` of `*` into that folder, keeping them out of git without editing the repo's own files. On yes, run `python3 <report.py> <temp>/run.json --save <label>` and show only its final `Saved …` line. Done when the run is saved or the offer declined.

6. **Compare**, only for `compare`. `python3 <compare.py> [skill] [--since YYYY-MM-DD] [--until YYYY-MM-DD]`: it reads the saved runs in `~/.agents/telemetry/` and this repo's `.agents/telemetry/`, and prints one table per skill (`plug:deploy` and `deploy` share one), one row per run oldest to newest (a session's automatic save stands for the whole session; without one, each manual save of it is its own run; that run's spans of the skill summed, cost at today's prices, `unknown` for a model with no price), then a `change` row: latest minus earliest. Exit code 2 means no saved run matches: relay its message and stop. Show the output verbatim in a code block, then one plain line on what changed most (the figure with the largest relative change, and in which skill). Done when the tables and the line are shown.

7. **Analyse**, only for `analyse`. `python3 <analyse.py> <skill> [--top N]`: it reads the same saved runs as step 6, keeps those where the skill ran, and lists that skill's steps in four tables, each row naming where the step ran (the subagent, else the skill) and how many runs show it: **Slowest** (median and max seconds, top N by median, default 10; questions, subagent launches and background steps left out), **Failing** (failed out of total), **Repeated** (the same step twice or more within one run) and **Retries** (a failed step run again nearly the same within the next 3 steps). Repeated and Retries leave out steps with an empty label (MCP tools, `AskUserQuestion`), since nothing shows two of them are the same. An empty table prints `none`; saved runs without steps are skipped and counted on the last line. Exit code 2 means no saved run of the skill was found: relay its message and stop. Show the output verbatim in a code block. Then read the skill's `SKILL.md` (and the brief of a subagent named in `Where`) and, for the top findings (at most five, worst first), name the step they likely come from and one concrete change: drop a repeated read by keeping what the first one returned, fix the failing command, add a check before a retried step, or move a slow step to a script or a subagent. Say plainly when a finding matches no step. Done when the tables are shown and each top finding has its step and change, or the message for exit 2 is relayed.
