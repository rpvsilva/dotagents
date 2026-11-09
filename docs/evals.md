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
| `--tool` | required | The tool adapter in `evals/_runner/adapters/`: `claude`. |
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

The tool authenticates from env vars only, the ones its adapter names: `CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY` for Claude Code. They reach the tool's process and nothing else: not its argv, not the checks, and not a saved file, where every value is replaced by `[redacted]`. No credentials file is copied into the run.

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
- `secs` is enforced by running the tool under [`scripts/capped`](../scripts/capped) `--secs`; its `capped: killed: timeout` line marks the hit.

A run that hits a cap is saved, not dropped, with the cap's name in `capped` (`turns`, `cost` or `time`) and `passed: false`.

### Checks

After the tool exits, `evals/_runner/checks.py` runs each check in `work/`, with stdin closed and up to 300 s each (longer fails it). A check's env holds only `PATH`, `HOME`, `XDG_CONFIG_HOME`, `CLAUDE_CONFIG_DIR`, `LANG`, `TMPDIR` and the `EVAL_*` paths of [Paths in prompts and checks](#paths-in-prompts-and-checks): no auth var. The run passes when every check, `no-github` included, passes and no cap or error stopped it.

### Cost ceiling

`--budget-usd` caps what one invocation spends. Once the summed cost of the runs it made reaches it, no new run starts: the results saved so far stay, the remaining runs are named on the last line, and the command exits 1. A reused run and an install failure cost nothing against it; a run whose cost is unknown counts as its case's `cost_usd` cap.

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

A saved run is reused, with no tool call, when the tool, the ref's commit, the model and every file of the case (the case hash) are unchanged and its `result.json` exists with no `error`. So a second run of `main` costs nothing, and a run that errored (an install or tool failure, often the environment's) is made again. A change to any of them makes a new folder, and `--fresh` runs again over a saved one.
