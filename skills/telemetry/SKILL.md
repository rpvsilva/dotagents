---
name: telemetry
description: Measure the run that just finished (wall clock, turns, tool calls, tokens, cost), compare it with another, and save it as a JSON baseline.
disable-model-invocation: true
---

Report what the **run** cost: the stretch of this session from its start, or from a named skill's last invocation, up to this telemetry request, subagents included. Scripts compute every number; you relay them and read them.

Arguments, all optional: a **skill** name to measure from; a **baseline** to compare against (a saved label, a JSON path, or a session ID); `--save <label>`; `--session <id>` to measure an older session instead of this one.

## Steps

1. **Find the scripts.** Resolve this skill folder's real path (`cd <skill folder> && pwd -P`): it sits in the dotagents repo at `skills/telemetry/`. Beside it is `report.py`; the extractor for the tool you are running in is `adapters/<tool>/telemetry/extract.py` from the repo root (`claude` for Claude Code). If your tool has no extractor, say so and stop. Done when both paths exist.

2. **Extract the run.** `python3 <extract.py> [--skill <name>] [--session <id>] > <temp>/run.json`. Exit code 2 means the skill never ran in that session: relay the list of skills that did and stop. Done when `run.json` holds the run.

3. **Get the baseline**, only when one was asked for:
   - saved label: `.agents/telemetry/<label>.json` in the current repo (list that folder when the label doesn't match a file);
   - JSON path: use it as is;
   - session ID: run step 2 against it into `<temp>/baseline.json`.

4. **Report.** `python3 <report.py> [<baseline.json>] <temp>/run.json [--save <label>]`, baseline first so the change column reads baseline → this run. Show the output verbatim in a code block. Then add two or three plain lines on what drove the numbers: the biggest cost line (output, cache writes, cache reads), the busiest tool or subagent, and with a baseline, the change that matters most.

5. **Check prices**, only when the report ends with a `check with: update_pricing.py …` line (prices over 30 days old, or a model with no price). Run that command from the skill folder: it compares `pricing.json` with LiteLLM's price list and writes nothing. Show its changes and ask whether to apply them; on yes, re-run it with `--apply`, then re-run step 4. If it prints `LiteLLM unavailable`, say prices stay as they are and go on. Done when prices are applied, declined, or unavailable.

6. **Offer to save**, unless `--save` was already given: ask once whether to save this run as JSON for later comparisons, and for a label. On yes, run `python3 <report.py> <temp>/run.json --save <label>` and show only its final `Saved …` line.
