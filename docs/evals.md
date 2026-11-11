# 🧪 Evals

[← Back to README](../README.md)

An **eval** runs a skill on a fixed task and checks what it leaves behind. It tells you whether a change to a skill, a brief or a Setting default made the skill better or worse, measured on pass rate, cost, turns and time.

Evals are dev tooling, like `tests/`:

- They live in one top-level `evals/` folder: the cases, and the runner in `evals/_runner/`.
- They are not a Part and belong to no Group: no `needs.json`, and `install.sh` links nothing from `evals/`. `tests/repo.test.sh` fails if a part ships a file under `evals/`.
- They run on your machine, only when you ask. CI never runs them.

## Eval cases

An **Eval case** is one folder, `evals/<skill>/<case>/`:

```
evals/configure/repo-max-parallel/
  case.json      the model, caps and optional fields below
  prompt.md      what a user would type
  fixture/       the repo the run starts in, as plain files
  input/         optional: data the prompt names (an epic, its tickets) as JSON and Markdown
  checks/        one executable per check
  rubric.md      optional: criteria a judge model scores
```

- `<skill>` must name a skill: `skills/<skill>/SKILL.md` exists.
- Folders `evals/_*` hold tooling, not cases.
- `fixture/` holds plain files: no `.git` (the run makes the repo) and no `test_*.py` (`tests/python.test.sh` would run it).
- `checks/` holds one check per executable file, named by its stem (`checks/settings-written.sh` is the check `settings-written`). A file that is not executable is not a check, and a case needs at least one. Two checks may not share a stem, and `no-github` is the built-in check's name (see below).

## `case.json`

```json
{
  "model": "claude-sonnet-5-5",
  "caps": { "turns": 20, "secs": 600, "cost_usd": 1 },
  "settings": { "build-epic": { "max_parallel": 2 } }
}
```

| Field | Default | Meaning |
|---|---|---|
| `model` | required | The model the case runs on, as a full ID such as `claude-sonnet-5-5`. |
| `caps` | required | The run's caps, all three required and positive: `turns` (an integer), `secs` and `cost_usd` (numbers). A run that hits one is recorded with the cap's name, not dropped. |
| `prompt` | `prompt.md` | The file holding the prompt; it must exist and not be empty. |
| `runs` | `3` | How many times the case runs, a positive integer. |
| `settings` | `{}` | Settings the run uses, written as the fixture's repo Level file `.agents/settings.json`. Same shape as that file: see [Settings](settings.md). |
| `install` | `["<skill>"]` | The parts installed in the run's throwaway home, as a non-empty list of names `scripts/parts.sh list` prints, never `global`. Core always comes along, as with `install.sh`. |
| `rubric` | `rubric.md` when it exists, else none | The file a judge model scores; when named, it must exist. |
| `judge_model` | `claude-haiku-5-5` | The model that scores the rubric, as a full ID. |

Any other key is an error.

A full model ID holds a digit and does not end in `latest`; a `claude-*` ID must also be a key of `models` in [`skills/telemetry/pricing.json`](../skills/telemetry/pricing.json), so its cost can be computed. `sonnet`, `opus`, `default` and `claude-sonnet-latest` are aliases and fail: an alias moves to a new model without the case changing, and results stop being comparable.

## Paths in prompts and checks

The paths a run uses exist only at run time. A prompt names them with placeholders, and each check script gets them as env vars:

| Prompt | Check env | What it is |
|---|---|---|
| `{work}` | `EVAL_WORK` | The copy of `fixture/` as a git repo, outside the dotagents checkout. The tool's working folder, and each check's. |
| `{scratch}` | `EVAL_SCRATCH` | An empty folder outside `work`, e.g. a `build-epic` run folder. |
| `{input}` | `EVAL_INPUT` | A copy of the case's `input/`. |
| `{home}` | `EVAL_HOME` | The run's throwaway `$HOME`, with dotagents installed at the run's ref. |
| | `EVAL_TRANSCRIPT` | The run's transcript. |
| | `EVAL_GH_CALLS` | The file recording every `gh` call the run made. |
| | `EVAL_CHECKOUT` | The dotagents checkout at the run's ref, e.g. to call one of its scripts. |

A check passes when it exits 0. Keep it deterministic: it reads files, runs commands and compares; judgement goes in the rubric.

```bash
#!/usr/bin/env bash
# Passes when the repo Level file sets build-epic's max_parallel to 2.
[ "$(jq -r '."build-epic".max_parallel' "$EVAL_WORK/.agents/settings.json")" = 2 ]
```

## No network, no GitHub

A case needs nothing beyond the tool's own model API: no network fetch and no GitHub, through `gh` or otherwise, stubbed or real. A case tests only the GitHub-free part of a skill; a `build-epic` case, for example, gets its epic as a file in `input/`.

The run puts a `gh` that fails and records the call first on `PATH`. Every case gets a built-in check, `no-github`, that fails when `EVAL_GH_CALLS` holds a call. A `gh` called by its absolute path bypasses the stub: a known gap.

## Checking cases

```bash
python3 -I evals/_runner/cases.py check
```

It prints `ok`, or one line per problem (`<case path>: <problem>`) and exits 1. `tests/repo.test.sh` runs it, so a malformed case fails the checks: a missing prompt or checks, an unknown skill, a model alias, an unknown key or a bad value.

## Run cases

```bash
python3 -I evals/run.py --tool claude --ref <ref> [--runs N] [--model <id>] [--budget-usd 10] \
  [--results <dir>] [--fresh] [--evals <dir>] (--all | <skill>/<case>...)
```

It runs each named case, or every case with `--all`, against dotagents at a committed ref, `--runs` times each, and saves one result per run.

| Flag | Default | Meaning |
|---|---|---|
| `--tool` | required | The tool adapter in `evals/_runner/adapters/`: `claude`, `codex` ([Codex](#codex)) or `cursor` ([Cursor](#cursor)). |
| `--ref` | required | The git ref to test, resolved to its commit. Only committed refs: the run extracts it with `git archive`, so uncommitted changes are not in it. |
| `--runs` | the case's `runs` | How many times each case runs, at least 1. |
| `--model` | the case's `model` | Overrides the model of every case, as a full ID (an alias fails). Required on a tool whose adapter does not run the case's Claude model. |
| `--budget-usd` | `10` | The cost ceiling for the whole invocation, over 0 (see below). |
| `--results` | `EVALS_RESULTS`, else `~/.agents/evals/results` | The results store. |
| `--fresh` | off | Runs again even when a saved run matches; the saved run is replaced only once the new one has its result. |
| `--evals` | this repo's `evals/` | Another folder of cases. |
| `--all` | off | Every case under `--evals`. Give `--all` or case names, not both. |
| `-h`, `--help` | | Prints the usage. |

Before any run, it refuses an unknown tool or case, a malformed case, a model alias, a prompt starting with `-`, and install parts that pull in `global`. Each run then prints one line:

```
configure/repo-max-parallel  run 1  pass  $0.0412  turns 6  38.2s  cap -
```

That is the case, the run number, `pass` or `fail`, the cost (`unknown` when it cannot be priced), the turns and seconds (`?` when unknown) and the cap it hit (`-` for none), then `error <kind>` when the run errored and `(saved)` when a saved run was reused.

| Exit | Meaning |
|---|---|
| `0` | Every run was made or reused, whatever its checks gave. |
| `1` | The cost ceiling skipped runs; the last line names them. |
| `2` | A usage error: nothing ran. |

### Auth

The tool authenticates from env vars only, the ones its adapter names: `CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY` for Claude Code, `CODEX_API_KEY` for Codex, `CURSOR_API_KEY` for Cursor. They reach the tool's process and nothing else: not its argv, not the checks, and not a saved file, where every value is replaced by `[redacted]`. No credentials file is copied into the run.

### What a run starts from

Each run gets its own temp folder, outside the dotagents checkout:

- `home/`: a throwaway `$HOME`. The adapter prepares it (Claude's `~/.claude`), then the ref's own `install.sh <tool> <parts>` installs the case's `install` parts and core into it, without plugins, never with no parts and never `global`. No personal memory, instructions, plugins or Settings leak in: `XDG_CONFIG_HOME` and `CLAUDE_CONFIG_DIR` point inside it, so the run's machine [Level](settings.md#where-values-live-levels) is `home/.config/dotagents/settings.json`, never your real one. An install failure is saved as `error: install`, not as a case failure.
- `work/`: the case's `fixture/` as a fresh git repo with one commit, the case's `settings` in it as `.agents/settings.json`. The tool's working folder.
- `scratch/`, empty, and `input/`, a copy of the case's `input/`.
- `bin/gh`, the `gh` stub first on `PATH` (see [No network, no GitHub](#no-network-no-github)).

The tool's environment holds only `PATH`, `HOME`, `XDG_CONFIG_HOME`, `CLAUDE_CONFIG_DIR`, `LANG`, `TMPDIR` and the adapter's auth vars. The [case format](#eval-cases) and the [placeholders](#paths-in-prompts-and-checks) say what a case gives the run.

### Caps

Each run has the case's three caps:

- `turns` and `cost_usd` go to the tool's own flags (Claude Code's `--max-turns` and `--max-budget-usd`); the adapter reads a hit from the transcript.
- After every run, on any tool, `run.py` compares the run's `turns` and `cost_usd` with the caps: one over its cap is saved as `capped: turns` or `capped: cost`. This is how a tool with no such flag (Codex) is capped; an unknown value is never over.
- `secs` is enforced by running the tool under [`scripts/capped`](../scripts/capped) `--secs`; its `capped: killed: timeout` line marks the hit.

A run that hits a cap is saved, not dropped, with the cap's name in `capped` (`turns`, `cost` or `time`) and `passed: false`.

### Checks

After the tool exits, `evals/_runner/checks.py` runs each check in `work/`, with stdin closed and up to 300 s each (longer fails it). A check's env holds only `PATH`, `HOME`, `XDG_CONFIG_HOME`, `CLAUDE_CONFIG_DIR`, `LANG`, `TMPDIR` and the `EVAL_*` paths of [Paths in prompts and checks](#paths-in-prompts-and-checks): no auth var. The run passes when every check, `no-github` included, passes and no cap or error stopped it.

### Cost ceiling

`--budget-usd` caps what one invocation spends. Once the summed cost of the runs and judges it made reaches it, no new run or judge starts: the results saved so far stay, the remaining runs are named on the last line, and the command exits 1. The judge's cost ([The judge](#the-judge)) counts like the run's, so a judge can be what tips the budget. A reused run and an install failure cost nothing against it, unless the reused run is judged again (below); a run whose cost is unknown counts as its case's `cost_usd` cap, a judge whose cost is unknown as its $0.50 cap.

### Results

Results live in the store, never in the repo, and are never committed:

```
<store>/<tool>/<ref sha>/<model>/<skill>/<case>/<case hash>/run-<n>/
  result.json        what the run came to (below)
  transcript.jsonl   the tool's output
  stderr.log         the tool's stderr
  work/  scratch/  home/  gh-calls    the run's end state
```

`home/` keeps its real files only: the links `install.sh` made into the checkout are dropped. Every kept file has each auth value replaced by `[redacted]`, so a token never lands in a result. The kept state is what grading re-runs the checks on.

`result.json` holds the tool, `tool_version`, `model`, `ref` and `ref_sha`, the `case`, `case_hash` and `run`, `checks` (`{name: true|false}`) and `checks_hash`, `passed`, `tokens` per model, `cost_usd` (computed from [`skills/telemetry/pricing.json`](../skills/telemetry/pricing.json)) with `cost_basis` and `pricing_as_of`, `turns`, `duration_s`, `capped`, `subagents` (`used`, `fallback` when the skill lists `agents` and none started, or `none`), `error`, `rubric` and the `transcript` path. An unknown value is `null`, never 0.

### Reuse and `--fresh`

A saved run is reused, with no tool call, when the tool, the ref's commit, the model and the case hash are unchanged and its `result.json` exists with no `error`. So a second run of `main` costs nothing, and a run that errored (an install or tool failure, often the environment's) is made again. A change to any of them makes a new folder, and `--fresh` runs again over a saved one.

The case hash covers only what changes a run: every file of the case except `checks/` and the rubric (`rubric.md`, or the file `case.json`'s `rubric` names), and every field of `case.json` except `rubric`, `judge_model` and `runs`. Those are graded, not run, so editing them never pays for the case again: a reused run whose `checks_hash`, `rubric_hash` or `judge_model` differs from the case's is re-graded first, as [`grade.py`](#grade-runs) does. Its checks run again on its kept state, the judge runs again only when the rubric or `judge_model` changed, and the case's tool is never called. A changed `runs` reuses the saved runs it still counts and makes only the new ones.

### Codex

`--tool codex` runs the same cases, checks unchanged, on Codex's headless mode, `codex exec --json`, through `evals/_runner/adapters/codex.py`:

```bash
CODEX_API_KEY=... python3 -I evals/run.py --tool codex --ref main --model gpt-5.1-codex configure/repo-max-parallel
```

- **Auth**: `CODEX_API_KEY` alone, reaching the `codex` process only, as in [Auth](#auth). The home is throwaway, so a saved `codex login` is never read.
- **`--model` is required.** A case's `model` is a Claude ID, which Codex cannot run, so the command refuses to start without `--model <full model ID>`. It goes to `codex exec -m`, and the results are filed under it.
- **Home**: the adapter makes `~/.codex` in the run's home, then `install.sh codex <parts>` installs the case's parts into it.
- **Caps**: Codex has no turn or cost cap flag, so only `secs` stops a run, by `capped`. `turns` counts the run's `turn.completed` events, and a run over its `turns` or `cost_usd` cap is saved as `capped: turns` or `capped: cost` after the fact ([Caps](#caps)). `duration_s` is the wall clock time.
- **Tokens**: from the last `turn.completed` event's `usage` alone, never a sum over turns: Codex reports the thread's running total there (`usage_from_last_total` in `codex-rs/exec/src/event_processor_with_jsonl_output.rs`, [openai/codex](https://github.com/openai/codex) at `9f04d88`, as the adapter cites). `input_tokens` less `cached_input_tokens` is `input`, `cached_input_tokens` is `cacheRead`, and `output_tokens` alone is `output`: it already holds the reasoning tokens (`reasoning_output_tokens` is their share of it, `codex-rs/codex-api/src/sse/responses.rs`), so adding them would count them twice. Codex reports no cache writes.
- **Model and version**: the events name neither, so both come from the session log Codex writes for the thread (`~/.codex/sessions/**/rollout-*-<thread id>.jsonl`). Without it, `tool_version` is `null` and the tokens are kept under the model `unknown`.
- **Cost is `unknown`** until [`skills/telemetry/pricing.json`](../skills/telemetry/pricing.json) has a row under `models` for the model the session log names, with all five kinds (`input`, `output`, `cacheRead`, `cacheWrite5m`, `cacheWrite1h`, in USD per million tokens; Codex reports no cache writes, so their price never counts). [`update_pricing.py <model> --apply`](../skills/telemetry/update_pricing.py) adds it from LiteLLM's price list only when that gives every kind's price, cache writes included; otherwise add it by hand. Until then a run's cost counts as its case's `cost_usd` cap against `--budget-usd` ([Cost ceiling](#cost-ceiling)).
- **Subagents**: a run's subagents are its `collab_tool_call` items that spawn an agent, each counted once. When the skill lists `agents` and the run started none, `subagents` is `fallback`: the skill ran its documented one-by-one fallback, following each brief itself ([Subagents](../skills/_shared/conventions.md#subagents)), as on a harness with no subagents.
- **Final answer**: the text of the run's last completed `agent_message` item, what a judge reads as the run's answer.
- **A tool error**: a run whose last turn event is not `turn.completed` (a `turn.failed`, an `error` event, or a `turn.started` with no end: a run killed or cut off mid-turn, even after earlier turns completed) is saved with `error: tool`, keeping the tokens it spent.

Not yet proven by a real run, only by Codex's docs and source and by hand-written transcripts: the flags `--sandbox workspace-write`, `--skip-git-repo-check` and `-m`, the session log's path and its `turn_context` and `session_meta` fields, and the `collab_tool_call` items that mark a subagent. The first real run confirms them, or fixes the adapter.

### Cursor

`--tool cursor` runs the same cases, checks unchanged, on Cursor's headless mode, `cursor-agent -p --output-format stream-json`, through `evals/_runner/adapters/cursor.py`:

```bash
CURSOR_API_KEY=... python3 -I evals/run.py --tool cursor --ref main --model gpt-5.2 configure/repo-max-parallel
```

- **Auth**: `CURSOR_API_KEY` alone, reaching the `cursor-agent` process only, as in [Auth](#auth): never `--api-key`, so the key stays out of argv. The home is throwaway, so a saved `cursor-agent login` is never read.
- **`--model` is required.** A case's `model` is a Claude ID, which the command will not pass to Cursor, so it refuses to start without `--model <full model ID>`. It goes to `cursor-agent --model`, and the results are filed under it.
- **Home**: the adapter makes `~/.cursor` in the run's home, then `install.sh cursor <parts>` installs the case's parts into it. That links subagents, hooks and MCP servers, not skills: see the [Cursor adapter](../adapters/cursor/README.md) for what is wired.
- **Caps**: Cursor has no turn or cost cap flag, so only `secs` stops a run, by `capped`. `turns` counts the run's `assistant` events (one per complete message, so an approximation), and a run over its `turns` cap is saved as `capped: turns` after the fact ([Caps](#caps)). `duration_s` is the `result` event's `duration_ms`.
- **Tokens**: none. Cursor's `stream-json` and `json` output carry no token usage, so `tokens` is `null`.
- **Model and version**: the results are filed under `--model`; the events name no tool version, so `tool_version` is `null`.
- **Cost is `unknown`, never 0**: with no tokens there is nothing to price, so `cost_usd` is `null` with `cost_basis` `unknown`, and the run line prints `unknown`. A free run and an unpriced one must not look alike, so a 0 would be wrong. A run's cost counts as its case's `cost_usd` cap against `--budget-usd` ([Cost ceiling](#cost-ceiling)), and the `cost_usd` cap never fires after the fact, since an unknown value is never over.
- **Subagents**: a run's subagents are its started `tool_call` events that start an agent: a `taskToolCall` or `agentToolCall`, or a `function` named `Task` or `Agent`. When the skill lists `agents` and the run started none, `subagents` is `fallback`, as for [Codex](#codex).
- **Final answer**: the text of the run's last `assistant` message holding any, what a judge reads as the run's answer; not the `result` event's `result`, which a failed or cut run lacks.
- **A tool error**: a run whose stream has no `result` event (killed, crashed or cut off) or whose `result` has `is_error: true` is saved with `error: tool`.

Not yet proven by a real run, only by Cursor's docs and `cursor-agent --help` and by hand-written transcripts: the flags `--force`, `--trust` and `--workspace`, the event shapes above, the subagent tool names, and whether headless `-p` loads the skills installed in the run's home at all. Until the first manual run settles that, a Cursor result says how the model did without the skill as readily as with it. The first real run confirms them, or fixes the adapter.

## Rubrics and re-grading

Checks say whether a run did the job; a rubric scores how well, by a judge model. The case names its rubric and judge model with the `rubric` and `judge_model` fields of [`case.json`](#casejson).

### Writing a rubric

`rubric.md` is Markdown. Each top-level list item (`-`, `*`, `+` or `1.`) is one criterion, written `- <name>: <what it asks>`:

```markdown
- Right level: the Setting lands in the repo Level file, not the machine one.
- **Says what changed**: the final answer names the key and its new value.
- Asks nothing
```

- The name is the text before the first `:`, with `**` and backticks dropped; an item with no `:` is its own name. A name used twice counts once.
- Nested items and other text are context the judge reads, not criteria.
- A rubric with no criteria is saved as a rubric error, never a score.

Write each criterion so a reader of the run's answer, diff and scratch files (below) can score it; what a check can test belongs in `checks/`.

### The judge

When a case has a rubric, `run.py` judges each run it makes once the checks have run; a reused run keeps its saved rubric unless the rubric or `judge_model` changed since ([Reuse and `--fresh`](#reuse-and---fresh)). The judge:

- **Model**: the case's `judge_model`, default `claude-haiku-5-5`, recorded in the result. `--model` does not change it.
- **Tool**: runs on the run's own tool, through the same adapter and the same [Auth](#auth) env vars as the case, in a throwaway home and an empty work folder, both removed after, under `scripts/capped` with 2 turns, 300 s and $0.50.
- **On Codex or Cursor**: the default `claude-haiku-5-5` is a Claude ID those tools cannot run, so the judge fails and the run's `rubric` is saved as an error. Give such a case a `judge_model` its tool runs. No test yet covers a rubric case on either tool.
- **Cost**: its tokens priced by [`skills/telemetry/pricing.json`](../skills/telemetry/pricing.json), saved as the rubric's `cost_usd` (`null` when unknown), apart from the run's own `cost_usd`. It counts against `--budget-usd` in `run.py` and `grade.py`; an unknown one counts as the judge's $0.50 cap.

The judge sees only:

- the rubric and the case's prompt;
- the run's final answer, from its adapter's `answer(run_dir)`, cut to 16,000 bytes;
- `git diff` of the run's `work/` against the fixture commit, untracked files included, cut to 48,000 bytes;
- the files under `scratch/`, up to 32,000 bytes in all; the files past that, and binary ones, by name only.

It never sees the checks' results, the rest of the transcript or `home/`.

Every adapter must provide `answer(run_dir)` (the interface is in `evals/_runner/adapters/__init__.py`): the run's final answer from its transcript, or `None`. A new tool's adapter that lacks it cannot be judged.

The judge answers one JSON object, a score from 1 to 5 and a one-line reason per criterion. The run's `result.json` keeps it as `rubric`:

| Outcome | `rubric` |
|---|---|
| Judged | `{"scores": {"<criterion>": {"score": 1-5, "reason": "..."}}, "judge_model", "rubric_hash", "cost_usd"}` |
| The judge failed: no answer, not JSON, a criterion missing or extra, or a score out of range | `{"error": "<what went wrong>", "judge_model", "rubric_hash", "cost_usd"}` |
| No judge ran: it could not start, or the rubric has no criteria | `{"error": "<what went wrong>", "judge_model", "rubric_hash"}`, no `cost_usd` (costs nothing) |
| No rubric | `null` |

`rubric_hash` is the sha256 of the rubric file, so re-grading can tell when it changed, as `checks_hash` does for `checks/`; neither is part of the case hash.

### Grade runs

```bash
python3 -I evals/grade.py [--results <dir>] [--rejudge] [--ref <ref>] [--budget-usd 10] [--evals <dir>] \
                          (--all | <skill>/<case>...)
```

It grades saved runs again with the case's current checks and rubric. Grading never runs a case again: no tool call for the case itself, only the judge when a rubric score needs redoing. It works on every saved run of the named cases, whatever its tool, model or case hash.

| Flag | Default | Meaning |
|---|---|---|
| `--results` | `EVALS_RESULTS`, else `~/.agents/evals/results` | The results store to grade. |
| `--rejudge` | off | Judges every graded run with a rubric again, even when its rubric and `judge_model` are unchanged. |
| `--ref` | every ref | Grades only the runs made at this ref's commit. |
| `--budget-usd` | `10` | The cost ceiling for the judges this invocation starts, over 0 (see below). |
| `--evals` | this repo's `evals/` | Another folder of cases. |
| `--all` | off | Every saved run. Give `--all` or case names, not both. |
| `-h`, `--help` | | Prints the usage. |

For each run, it:

1. Runs the case's current `checks/` and `no-github` on the run's kept `work/`, `scratch/`, `home/`, `gh-calls` and transcript ([Results](#results)), with `EVAL_CHECKOUT` a fresh extract of the run's `ref_sha` and `EVAL_INPUT` the case's current `input/`. It rewrites `checks`, `checks_hash` and `passed`; a run that hit a cap or errored stays failed.
2. Judges the run again, on the run's tool, only when the case's rubric or `judge_model` differs from the saved one (by `rubric_hash` and `judge_model`), a rubric was added since, or with `--rejudge`. A case whose rubric is gone gets `rubric: null`.
3. Keeps every other field of `result.json`.

Re-grading needs the run's kept state: delete a run's `work/`, `home/` or `scratch/` and its checks have nothing to read. A run saved with `error: install` has no state, so it is left as it is. A run whose case folder is gone, or whose `result.json` does not read, is named and skipped.

Once the judges' summed cost reaches `--budget-usd` (an unknown one counting as the judge's $0.50 cap), a run that needs the judge is no longer graded: it is left as it is, named with `not graded: budget ...`, and the command exits 1. Re-running checks costs nothing, so a run needing no judge is always graded. `--rejudge` and `--all` together judge every saved run with a rubric: narrow it with case names or `--ref`, or raise the budget.

Each run prints one line:

```
configure/repo-max-parallel  run 1  claude claude-sonnet-5-5 3f2a9c1d0b4e  pass  rubric judged
```

That is the case, the run number, the tool, model and the first 12 characters of the commit, `pass` or `fail`, and the rubric: `judged` (judged now), `kept` (the saved score stands) or `none`, with `(error)` when the rubric holds an error.

| Exit | Meaning |
|---|---|
| `0` | Every run found was graded or skipped. |
| `1` | A run could not be graded: its commit is missing from this repo, or the budget was reached before its judge. |
| `2` | A usage error, a bad `--ref`, an unknown case or a named case with no saved run: nothing graded. |

## Compare two refs

```bash
python3 -I evals/compare.py --tool claude --base <ref> --head <ref> [--runs N] [--model <id>] \
  [--base-model <id>] [--head-model <id>] [--budget-usd 10] [--results <dir>] [--out <file.md>] \
  [--html <file.html>] [--evals <dir>] (--all | <skill>/<case>...)
```

It runs each named case, or every case with `--all`, on both refs and reports, per case, whether the head is better, worse or within noise of the base. Use it to judge a change to a skill: `--base main --head <your branch>`.

| Flag | Default | Meaning |
|---|---|---|
| `--tool` | required | The tool adapter, as for [Run cases](#run-cases). |
| `--base` | required | The ref to compare against, e.g. `main`, resolved to its commit. Only committed refs. |
| `--head` | required | The ref under test, resolved to its commit. Only committed refs. |
| `--runs` | the case's `runs` | How many times each case runs on each ref, at least 1. |
| `--model` | the case's `model` | The model of every case on both refs, as a full ID. Required on a tool whose adapter does not run the case's Claude model. |
| `--base-model` | `--model` | The base ref's model alone, e.g. to compare two models on one ref. |
| `--head-model` | `--model` | The head ref's model alone. |
| `--budget-usd` | `10` | One cost ceiling for both refs, over 0, spent in the order the runs are made ([Cost ceiling](#cost-ceiling)). |
| `--results` | `EVALS_RESULTS`, else `~/.agents/evals/results` | The results store, read for saved runs and written with new ones. |
| `--out` | `<store>/reports/<tool>-<base sha>-<head sha>.md`, with each sha's first 12 characters | Where the Markdown report is written. |
| `--html` | none | Also writes the report as one self-contained HTML page there. |
| `--evals` | this repo's `evals/` | Another folder of cases. |
| `--all` | off | Every case under `--evals`. Give `--all` or case names, not both. |
| `-h`, `--help` | | Prints the usage. |

Before any run, it checks both refs as `run.py` would ([Run cases](#run-cases)), so a case the head breaks is refused before the base's runs are paid for. It also refuses a report path (`--out` or `--html`) inside the repo: reports are never committed.

The refs take turns, one run number at a time: run 1 of every case on the base, then on the head, then run 2, and so on. Drift over the session (API load, time of day) then falls on both refs alike instead of on the head alone.

Run lines go to stderr as in [Run cases](#run-cases), then the path of each report written. The Markdown report goes to stdout.

| Exit | Meaning |
|---|---|
| `0` | Every run was made or reused. |
| `1` | The cost ceiling skipped runs; the report is still written, from the runs made. |
| `2` | A usage error, an unknown ref, tool or case, a model alias, or a report path inside the repo: nothing ran. |

### Saved runs are reused

Each ref runs through `run.py`'s own code, so a saved run is reused exactly as [Reuse and `--fresh`](#reuse-and---fresh) says, keyed by the tool, the ref's commit, the model and the case hash. Comparing a new branch against `main` a second time pays only for the new head: `main`'s runs are reused. The same ref on both sides runs once. Editing a case's checks or rubric re-grades the saved runs of both refs without calling the tool for the case; only the judge runs, when the rubric changed. `compare.py` has no `--fresh`: to run a ref again, use `run.py --fresh` on it first.

### Prices

Every run of both refs, saved or new, is priced again from its saved tokens with the current [`skills/telemetry/pricing.json`](../skills/telemetry/pricing.json), so a price change between the two refs' runs never shows as a cost difference. The report names the table's `asOf` date. A run with no tokens, or a model with no price, costs `unknown`, never 0.

### Verdicts

Each case gets one verdict, from its metrics: pass rate, rubric score per criterion, cost, turns and time. Higher pass rate and rubric are better; lower cost, turns and time are better.

A metric's change is **real** when:

- **pass rate**: the number of passing runs moves by at least two-thirds of the runs per case, rounded up (2 of 3, 4 of 5, 7 of 10). The runs per case are the larger side's;
- **rubric score**: the two refs' min-max ranges do not overlap;
- **cost, turns and time**: the ranges do not overlap, and the median moves by at least 10% of the base's. Without that floor, two tight ranges a fraction of a cent apart would count as real.

Otherwise it is noise. The first metric with a real change, in the order pass rate, rubric, cost, turns, time, decides the verdict: `better` or `worse`. So a pass rate gain outranks a higher cost. Rubric criteria that are real in both directions decide nothing, and the next metric is tried. With no real change, the verdict is `noise`. A case that ran on one ref only is `unknown`.

A `noise` case is **worth more runs** when the pass rate or a rubric median differs between the refs, or a cost, turns or time median moves by at least 10%: there may be a change too small to show at this many runs. Run it again with a higher `--runs`; the saved runs are reused and only the new ones are made.

### Reading the report

The report starts with a header naming the tool, the model (and each ref's when they differ), both refs with their commits, the runs per case and the price table's date.

Then one row per case, and one row per rubric criterion under it:

```
| case | pass rate | cost | turns | time | rubric | verdict |
|---|---|---|---|---|---|---|
| configure/repo-max-parallel | 33% → 100% (+67%) | $0.05 ($0.04–$0.06) → $0.03 ($0.03–$0.04) (-$0.02) | 8 (7–9) → 6 (5–6) (-2) | 45 s (40 s–50 s) → 31 s (30 s–32 s) (-14 s) |  | better |
| configure/repo-max-parallel · right-level |  |  |  |  | 3 (3–4) → 4 (4–5) (+1) | noise |
```

Each cell reads `base → head (difference)`. A metric shows its median, with its min-max in brackets when the runs differ; the difference is head's median less base's. Each rubric criterion's row gives its score from 1 to 5 the same way ([Rubrics and re-grading](#rubrics-and-re-grading)) and that criterion's own verdict. A value no run gave prints `unknown`, never 0.

In the example, the pass count moved from 1 to 3 of 3, a real change, so the case is `better`; the rubric's ranges overlap, so that criterion alone is `noise`.

The report ends with **Worth more runs**: the cases to run again, or `No case.`

## When evals run

Evals bill a model per run, so nothing runs them unless you ask. CI (`.github/workflows/ci.yml`) never runs them: `tests/repo.test.sh` fails if a workflow names `evals/` or `EVALS`.

### What a change picks

`evals/pick.py` prints the cases a change affects, one `<skill>/<case>` per line:

```bash
python3 -I evals/pick.py [--all | --base <ref> | -- <path>...]
```

It takes the changed files from `tests/affected.sh --files` with the same arguments: since the merge base with `origin/main` (committed, uncommitted or untracked), since `--base <ref>`, or the paths after `--`. [`AGENTS.md`'s Checks](../AGENTS.md#checks) says how `tests/affected.sh` picks them. Then:

| Changed file | Picks |
|---|---|
| Under `skills/<s>/`, but not a `test_*` file or one under `fixtures/` | every case of `<s>` |
| Under `evals/<s>/`, `test_*` files included (`evals/<s>/<case>/checks/test_x.sh`) | every case of `<s>` |
| A file another part ships (`scripts/parts.sh files`), when that part is in the case's `scripts/parts.sh closure --optional` of its skill and its `install` parts: a part the skill links to or needs (`skills/review-pr/reviewer-brief.md` for `build-epic`), a skill's helpers in `adapters/<tool>/<skill>/`, a part only the case installs. `test_*` files and `fixtures/` do not count. A core part's file counts when the case's skill or `install` names that part, or when the skill's own shipped files name the file by its repo path or its installed path (`~/.agents/bin/settings` for `scripts/settings.sh`, `~/.agents/hooks/<x>` for `scripts/hooks/<x>`), so `scripts/settings.sh` picks the `configure` cases. | that case |
| `skills/_shared/conventions.md`, anything under `evals/_runner/` but not under a `fixtures/` folder, an `evals/*.py` script | every case |
| A `test_*` file at the top of `evals/` (`evals/test_pick.py`) or anywhere under `evals/_runner/` (`evals/_runner/adapters/test_claude.py`), anything under a `fixtures/` folder in `evals/_runner/` | no case |
| `skills/_shared/settings.json` | the cases of every skill whose `settings.json` opts in with `{"shared": true}` to a key added, removed or changed since the merge base, and, when any key changed, every `configure` case (it shows and changes every Setting); every case when the changed keys cannot be read (no merge base, a missing or broken file) |
| A case whose `case.json` or parts cannot be read | that case, on any change |

`--all`, and a change with no merge base, pick every case. A change touching no skill with cases prints nothing.

### `EVALS` in `tests/run.sh`

`tests/run.sh` runs the evals last, after the suites, once, outside the macOS bash 3.2 pass (which would bill every run twice). The `EVALS` env var opts in, the way `REMOTE_SMOKE=1` opts in to `tests/remote-smoke.test.sh`:

| `EVALS` | What runs |
|---|---|
| unset | Nothing. The line `== evals: skipped (...)` names the cases `evals/pick.py` picks. |
| `1` | `evals/compare.py --tool claude --base origin/main --head HEAD` on the picked cases ([Compare two refs](#compare-two-refs)); `no case picked` when none. |
| `all` | The same compare on every case. |

Any other value is a usage error (exit 2). `tests/run.sh`'s arguments (`--all`, `--base <ref>`, `-- <path>...`) pass to `evals/pick.py` too. The compare runs commit `HEAD`, not the working tree: commit first. A failed compare fails `tests/run.sh`.

```bash
EVALS=1 tests/run.sh      # the suites, then the picked cases, HEAD against origin/main
EVALS=all tests/run.sh    # the suites, then every case
```

### After a model or tool update

A new model or a new tool version can move every case, whatever changed in the repo. Run the whole suite at once with the new model:

```bash
python3 -I evals/run.py --tool claude --ref main --all --model <full id>
```

To see what the update changed, compare the same ref on both models: `evals/compare.py --tool claude --base main --head main --base-model <old id> --head-model <new id> --all`.

## Pilot

The first run of the cases, to see whether evals are worth keeping (#378). The cases are the seven in this repo, built as [Eval cases](#eval-cases) says and run as [Run cases](#run-cases) says:

| Skill | Cases |
|---|---|
| `configure` | `build-epic-only`, `machine-three-agents`, `repo-two-agents`, `unknown-setting` |
| `build-epic` (planning only, epic given as a file, no GitHub) | `pass-a-remove`, `pass-a-rename`, `pass-b-top` |

### The run

- **Date:** 2026-11-10.
- **Commit:** `4bf92459a6cb391fb494e5407d9e5e8505e8f747`, the head of ticket 08's branch `feat/gh-378-eval-pilot` (the branch that adds the cases) when the run started; the `evals` integration branch's PR had not merged.
- **Tool and model:** Claude Code, `claude-sonnet-5-5`, 3 runs per case.
- **Command:** a same-commit compare ([Compare two refs](#compare-two-refs) with `--base HEAD --head HEAD`), so it measures noise and cost, not a change: `EVALS_RESULTS=<dir> python3 -I evals/compare.py --tool claude --base HEAD --head HEAD --all --runs 3 --budget-usd 40 --out <report.md> --html <report.html>`. `EVALS_RESULTS` was the run folder's `pilot/results/`, and `CLAUDE_CODE_OAUTH_TOKEN` came from the environment, never the command line.
- **Full report:** saved on the machine that ran it, never committed: `~/.agents/runs/cichlid/gh-372-skill-evals/pilot/report.md`, its page `report.html` beside it, and the runs under `pilot/results/claude/<commit>/claude-sonnet-5-5/`.

### Numbers

| Measure | Value |
|---|---|
| Pass rate | 7 of 7 cases passed 3/3; no cap hit, no budget stop |
| Total cost | $1.53 for all 21 runs (prices as of 2026-10-23), plus about $0.002 per Haiku rubric grade |
| Median cost per case | $0.064; case medians from $0.044 (`configure/machine-three-agents`) to $0.118 (`build-epic/pass-b-top`) |
| Median turns | 5 |
| Time per run | 5-35 s |
| Rubric scores | mostly 4-5 of 5; lowest `build-epic/pass-b-top` tracer-bullets at 3 (range 3-5) |

### What it showed

- The cases are stable: every run passed, so a failure on a later PR points at the change, not at noise.
- No check or prompt bug: no case needed a fix. A spot check of `pass-b-top` run 1 found the planner read its brief and wrote a real `map.md`, one ticket and two tasks with real `Writes` and `Check` lines.
- The costs are a floor. The fixtures are small (about 200 lines), so a real epic costs more.

### Outcome

The decision is made on the next three PRs that change a skill: each runs its picked cases with `EVALS=1 tests/run.sh` ([`EVALS` in `tests/run.sh`](#evals-in-testsrunsh)) and notes whether the compare caught something, missed something or only cost money. Then write here whether evals stay or go (#378).

| PR | Skill | What the compare showed |
|---|---|---|

**Decision:** not made yet.
