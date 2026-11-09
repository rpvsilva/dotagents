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
