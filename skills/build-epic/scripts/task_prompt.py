#!/usr/bin/env python3
"""Writes the prompt that starts a task's agent, here or remote, from one template.

Usage: python3 -I task_prompt.py <run> <TT-NN> [--remote] [--resolve <files>] [--sync <files>] [--cap <note>]
                                 [--failure <file>] [--reissue]

The agent is the `epic-implementer`; the prompt is the `implementer` role's (skills/_shared/protocol): the line
naming the agent and its brief, then its input object as JSON. Here, every input is its path on this machine and
`output` is `<run>/reports/<id>.json`. With `--remote`, the inputs name `~/work` for the worktree and
`~/attached/<name>` for every file sent along (attached by the printed arguments), and no `output`: the agent's final
message is its output object, which `remote-agent` brings back.

- `--resolve <files>`: a resolver's `resolve` input, the files comma-separated.
- `--sync <files>`: the `sync` input, for a hub task whose base merge `start_task.py` left conflicted in its
  worktree: the implementer resolves it before building. Local only (`sync-remote`): a remote run gets no merge state.
- `--cap <note>`: the `cap` input, `<kind> killed <cmd> (--mb <mb> --secs <secs>)`.
- `--failure <file>`: the `failure` input, naming a file that holds what failed (a red suite's log), sent along remotely.
- `--reissue`: the last run on this task did the work but its output broke the brief: its output is copied to
  `<run>/prompts/<id>-<n>-output.json` (kept even after the next run writes `reports/<id>.json`) and named by the
  `reissue` input.

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

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_shared" / "protocol"))
import protocol  # noqa: E402

USAGE = "[--remote] [--resolve <files>] [--sync <files>] [--cap <note>] [--failure <file>] [--reissue]"


def options(args):
    opts = {"remote": False, "reissue": False, "resolve": None, "sync": None, "cap": None, "failure": None}
    args = list(args)
    while args:
        flag = args.pop(0)
        if flag in ("--remote", "--reissue"):
            opts[flag[2:]] = True
        elif flag in ("--resolve", "--sync", "--cap", "--failure") and args and args[0].strip():
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
    if opts["sync"] and opts["remote"]:
        raise core.Fail("sync-remote")
    task = taskwt.Task(ctx)
    agent = protocol.schema("implementer")["agent"]
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
        output = run / "reports" / f"{ctx.id}.json"
        if not output.is_file() or output.stat().st_size == 0:
            raise core.Fail(f"no-output: {output}")
        reissue = prompts / f"{ctx.id}-{n}-output.json"
        shutil.copyfile(str(output), str(reissue))

    # A sandbox has no `<run>/env/`: a remote agent gets the line that makes its own (`setup.py`).
    test_command = (opts["remote"] and decisions.get(run, "remote_test_command")) or decisions.get(run, "test_command")
    inputs = {"task": sent(task.path)}
    if opts["remote"]:
        inputs["remote"] = True
        for name in ("spec.md", "map.md", "notes"):
            if (run / name).exists():
                inputs[Path(name).stem] = sent(run / name)
    else:
        inputs["run"] = str(run)
    inputs["worktree"] = "~/work" if opts["remote"] else str(task.worktree)
    inputs["ticket_branch"] = task.upstream
    if test_command:
        inputs["test_command"] = test_command
    for key in ("resolve", "sync"):
        if opts[key]:
            inputs[key] = re.split(r"[,\s]+", opts[key].strip(", "))
    if opts["cap"]:
        inputs["cap"] = opts["cap"]
    if failure:
        inputs["failure"] = sent(failure)
    if reissue:
        inputs["reissue"] = sent(reissue)
    if not opts["remote"]:
        inputs["output"] = str(run / "reports" / f"{ctx.id}.json")

    prompt = prompts / f"{ctx.id}-{n}.md"
    prompt.write_text(protocol.prompt("implementer", inputs, "~/.agents/skills" if opts["remote"] else None), encoding="utf-8")
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
