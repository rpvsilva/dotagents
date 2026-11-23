# gh-35 comparison fixture

The sources of the gh-35 two-run comparison (`~/.agents/runs/build-epic-sandbox/gh-35-comparison/`), for `test_compare_runs.py`.

- `<run>-telemetry.json`: the saved run, trimmed. Spans are whole. Subagent steps keep only `tool`, `subagent`, `failed` and `seconds`; orchestrator steps also keep `label` and `start`.
- `<run>-metrics.json`: the old `run-metrics.py` output, whole.
- `<run>-branch.json`: that run's entry of `branch-measurements.json`.
- `pricing.json`: the price table the comparison used.
- `expected.json`: `comparison.json` without `generatedAt`, `generator`, `epic`, `sources` or prose (conclusions, notes, descriptions, the estimate's `method`). Its roles are gh-35's names; the test maps them as spec Q14 says.

Every file has macOS home paths scrubbed: the home folder became `~`, and its dashed form in Claude temp paths became `-HOME`.
Session ids in the telemetry files are synthetic (`00000000-0000-4000-8000-00000000000N`), not those of the real runs.
