#!/usr/bin/env python3
"""Writes the prompt that starts a task's agent, here or remote, from one template.

Usage: python3 -I task_prompt.py <run> <TT-NN> [--remote] [--resolve <files>] [--cap <note>]
                                 [--failure <file>] [--reissue]

The agent is the `epic-implementer`. The prompt is the brief's
path, then one `<input>: <value>` line per input, the report file last. Here, every input is its path on this
machine and the report file is `<run>/reports/<id>.md`. With `--remote`, the same lines name `~/work` for the
worktree, `~/attached/<name>` for every file sent along (attached by the printed arguments), and `~/out/report.md`
for the report file, which `remote-agent` brings back after the run.

- `--resolve <files>`: a resolver's `resolve: <files>` line.
- `--cap <note>`: the cap note, `cap: <kind> killed <cmd> (--mb <mb> --secs <secs>)`.
- `--failure <file>`: a `failure:` line naming a file that holds what failed (a red suite's log), sent along remotely.
- `--reissue`: the last run on this task did the work but its report broke the brief: its report is copied to
  `<run>/prompts/<id>-<n>-report.md` (kept even after the next run writes `reports/<id>.md`) and named on a
  `reissue:` line.

The prompt goes to `<run>/prompts/<id>-<n>.md`, `<n>` one past the last. Prints
`OK <id> local prompt=<file> agent=<agent>`, or `OK <id> remote prompt=<file> args=<arguments>`: the arguments to
give the dispatch command (`--name`, `--agent`, each `--attach`, `@<file>` and the worktree), shell-quoted.
"""
import re
import shlex
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from steplib import core, decisions, taskwt  # noqa: E402

USAGE = "[--remote] [--resolve <files>] [--cap <note>] [--failure <file>] [--reissue]"
IMPLEMENTER = ("epic-implementer", "You are an epic-implementer. Follow ~/.agents/skills/build-epic/implementer-brief.md.")


def options(args):
    opts = {"remote": False, "reissue": False, "resolve": None, "cap": None, "failure": None}
    args = list(args)
    while args:
        flag = args.pop(0)
        if flag in ("--remote", "--reissue"):
            opts[flag[2:]] = True
        elif flag in ("--resolve", "--cap", "--failure") and args and args[0].strip():
            opts[flag[2:]] = args.pop(0).strip()
        else:
            raise core.Fail(f"usage: task_prompt.py <run> <id> {USAGE}")
    return opts


def next_number(prompts, ident):
    used = [int(m.group(1)) for f in prompts.glob(f"{ident}-*.md")
            if (m := re.fullmatch(re.escape(ident) + r"-(\d+)", f.stem))]
    return max(used, default=0) + 1


def step(ctx):
    opts = options(ctx.args)
    task = taskwt.Task(ctx)
    agent, first = IMPLEMENTER
    run = ctx.run_dir
    prompts = run / "prompts"
    prompts.mkdir(exist_ok=True)
    n = next_number(prompts, ctx.id)
    attach = []

    def sent(path):
        """The path as the agent reads it: as is here, `~/attached/<name>` remotely (and attached)."""
        if not opts["remote"]:
            return str(path)
        attach.append(str(path))
        return f"~/attached/{Path(path).name}"

    failure = None
    if opts["failure"]:
        failure = core.resolve(opts["failure"])
        if not failure.is_file():
            raise core.Fail(f"no-failure-file: {failure}")
    reissue = None
    if opts["reissue"]:
        report = run / "reports" / f"{ctx.id}.md"
        if not report.is_file() or report.stat().st_size == 0:
            raise core.Fail(f"no-report: {report}")
        reissue = prompts / f"{ctx.id}-{n}-report.md"
        shutil.copyfile(str(report), str(reissue))

    test_command = decisions.get(run, "test_command")
    lines = [first]
    lines.append(f"task: {sent(task.path)}")
    if opts["remote"]:
        for name in ("spec.md", "map.md", "notes"):
            if (run / name).exists():
                lines.append(f"{Path(name).stem}: {sent(run / name)}")
    else:
        lines.append(f"run: {run}")
    lines.append(f"worktree: {'~/work' if opts['remote'] else task.worktree}")
    lines.append(f"ticket branch: {task.upstream}")
    if test_command:
        lines.append(f"test_command: {test_command}")
    if opts["resolve"]:
        lines.append(f"resolve: {opts['resolve']}")
    if opts["cap"]:
        lines.append(f"cap: {opts['cap']}")
    if failure:
        lines.append(f"failure: {sent(failure)}")
    if reissue:
        lines.append(f"reissue: {sent(reissue)}")
    lines.append(f"report: {'~/out/report.md' if opts['remote'] else run / 'reports' / (ctx.id + '.md')}")

    prompt = prompts / f"{ctx.id}-{n}.md"
    prompt.write_text("\n".join(lines) + "\n", encoding="utf-8")
    ctx.note(prompt.read_text(encoding="utf-8"))
    if not opts["remote"]:
        return "local", f"prompt={prompt} agent={agent}"
    args = ["--name", task.path.stem, "--agent", agent]
    for path in attach:
        args += ["--attach", path]
    args += [f"@{prompt}", str(task.worktree)]
    return "remote", f"prompt={prompt} args={shlex.join(args)}"


if __name__ == "__main__":
    core.main("task_prompt", step, usage=USAGE)
