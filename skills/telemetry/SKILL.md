---
name: telemetry
description: Show what the run that just finished cost, split by skill: wall clock, turns, tool calls, tokens, cost; optionally save it as JSON.
disable-model-invocation: true
---

Report what the **run** cost: this session from its start (or from the previous telemetry report) up to this request, subagents included. The report splits the run into **spans**, one per skill the user typed; skills those load count toward them, and activity before any skill is the `no skill` span. Scripts compute every number; you relay them and read them.

Claude Code also saves every session's whole run on its own when the session ends, as `~/.agents/telemetry/<session id>.json` (a session that ends again overwrites its file), through the session-end hook `scripts/hooks/telemetry-save.sh`. The Setting `auto_save` turns that off: `~/.agents/bin/settings set telemetry auto_save false --machine` (or `--repo`), or the `configure` skill. The `--save` below is separate and unchanged.

Arguments, all optional: a **skill** name to report only that skill's last span; `--save <label>`; `--session <id>` to report on an older session instead of this one.

## Steps

1. **Find the scripts.** Resolve this skill folder's real path (`cd <skill folder> && pwd -P`): it sits in the dotagents repo at `skills/telemetry/`. Beside it is `report.py`; the extractor for the tool you are running in is `adapters/<tool>/telemetry/extract.py` from the repo root (`claude` for Claude Code). If your tool has no extractor, say so and stop. Done when both paths exist.

2. **Extract the run.** `python3 <extract.py> [--skill <name>] [--session <id>] > <temp>/run.json`. Exit code 2 means there is nothing to measure (the skill never ran, or nothing happened since the last report): relay its message and stop. Done when `run.json` holds the run.

3. **Report.** `python3 <report.py> <temp>/run.json [--save <label>]`. Show the output verbatim in a code block, then three plain lines: the costliest span and what drove it (output, cache writes or cache reads), the busiest tool or subagent, and the cheapest lever to try next. Done when the table and the three lines are shown.

4. **Check prices**, only when the report ends with a `check with: update_pricing.py …` line (prices over 30 days old, or a model with no price). Run that command from the skill folder: it compares `pricing.json` with LiteLLM's price list and writes nothing. Show its changes and ask whether to apply them; on yes, re-run it with `--apply`, then re-run step 3. If it prints `LiteLLM unavailable`, say prices stay as they are and go on. Done when prices are applied, declined, or unavailable.

5. **Offer to save**, unless `--save` was already given: ask once whether to save this run as JSON (to `.agents/telemetry/` at the repo root), and for a label. On yes, run `python3 <report.py> <temp>/run.json --save <label>` and show only its final `Saved …` line. Done when the run is saved or the offer declined.
