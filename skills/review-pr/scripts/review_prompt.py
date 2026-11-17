#!/usr/bin/env python3
"""Prints a reviewer's prompt for one review mode, in the agent protocol: the line naming the agent and its brief, then
the role's input object as JSON (`_shared/protocol`). File pointers only: no summary of the change.

Usage: python3 -I review_prompt.py <mode> [--pr <n>] --worktree <path> --head <sha> --base <ref>
         [--run <run>] [--decisions <file>] [--task <file>] [--findings <file>]
         [--output <file> | --remote <dir> --allow-author <login>...]

Modes, the role in brackets: `task` [reviewer] (one task PR; with no `--pr`, a task's local commits before they
merge, `--base` its ticket branch and `--task` required: build-epic's review of a high-tier task, which has no PR),
`ticket` [reviewer] (`--task` is the ticket file), `re-review` [reviewer] (`--base` is the head last reviewed; needs
`--findings`, the numbered earlier findings) and `security` [security, scope `pr`]. Every mode's `diff` input is the
command printing the diff from `git merge-base <base> <head>`. With `--run`, the inputs name `<run>/decisions.md`,
`<run>/map.md` (a reviewer's `map`, its Checks, Invariants and Field matrix; a security reviewer's `checks`, since its
`map` is a security map) and `<run>/spec.md` when it exists; without it, none of them, so standalone `review-pr`
works. `--decisions` names the decisions file instead of `<run>/decisions.md`, with or without `--run`; its `ci:` and
`test_command:` header lines, when set, become the `ci` and `test_command` inputs. `--output` (alias `--report`) names
the file the reviewer writes its output object to (a run's `<run>/reviews/<PR>-<sha7>.json`, `-security.json` for the
security reviewer), the only file it may write.

`--remote <dir>` renders the prompt for a reviewer run remotely, on a snapshot of the worktree with no history and no
GitHub access: `remote: true`, no `output` (its final message is its output object). It writes into `<dir>` the diff
(`pr-<n>.diff`) and the PR facts (`pr-<n>.json`, from `gh pr view <n> --json author,title,body,headRefOid,baseRefName,
files`), and `attach.txt`: the absolute path of each file to attach, one per line (those, the decisions file,
`<run>/map.md`, `<run>/spec.md` when it exists, `--task` and `--findings`), every basename distinct: an input whose
name is taken is copied into `<dir>` as `<its option>-<name>`. The inputs name the worktree as `~/work`, each file as
`~/attached/<name>` and the diff as `cat ~/attached/pr-<n>.diff`. A remote reviewer runs the PR's tests in a sandbox
holding a token, so `--remote` renders only a PR whose author is one of the `--allow-author` logins (repeatable, as
`gh pr view --json author` spells them; case aside): the human's and the run's identity. Any other author is refused
before the diff is written, and that reviewer runs locally, reading the whole diff before it runs anything.

Exits 2 with a usage line on an unknown mode, a missing option (`--pr` in any review but a local `task` one), a
`--findings` file that does not exist, `--remote` with `--output` or without `--allow-author`, or an input the role's
schema refuses; with `--remote`, exits 1 with a reason on stderr when `gh pr view`, the merge base or the diff fails,
the PR's author is not allowed, or an input to attach is missing.
"""
import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_shared" / "protocol"))

import protocol  # noqa: E402

MODES = ("task", "ticket", "re-review", "security")
PR_FIELDS = "author,title,body,headRefOid,baseRefName,files"
WORK = "~/work"
ATTACHED = "~/attached"


def absolute(path):
    """`path` made absolute against the caller's folder, since the reviewer runs elsewhere."""
    return os.path.abspath(os.path.expanduser(path))


def parse(argv):
    p = argparse.ArgumentParser(prog="review_prompt.py")
    p.add_argument("mode", choices=MODES)
    p.add_argument("--pr", type=int)
    p.add_argument("--worktree", required=True, type=absolute)
    p.add_argument("--head", required=True)
    p.add_argument("--base", required=True)
    p.add_argument("--run", type=absolute)
    p.add_argument("--decisions", type=absolute)
    p.add_argument("--task", type=absolute)
    p.add_argument("--findings", type=absolute)
    p.add_argument("--output", "--report", dest="output", type=absolute)
    p.add_argument("--remote", type=absolute)
    p.add_argument("--allow-author", action="append", default=[])
    args = p.parse_args(argv)
    if args.remote and args.output:
        p.error("--remote takes no --output: a remote reviewer returns its output object as its final message")
    if args.remote and not args.allow_author:
        p.error("--remote needs --allow-author: only a PR by the human or the run's identity is reviewed remotely")
    if not args.pr and (args.mode != "task" or not args.task or args.remote):
        p.error("--pr is required, except in a local task review with --task")
    if args.mode == "re-review" and not args.findings:
        p.error("re-review needs --findings")
    if args.findings and not os.path.isfile(args.findings):
        p.error(f"no findings file: {args.findings}")
    return p, args


def diff(worktree, base, head):
    """The command for the diff to review: from the merge base, so changes on the base since are not in it. Each value
    is quoted, since the reviewer runs the command in a shell."""
    worktree, base, head = shlex.quote(worktree), shlex.quote(base), shlex.quote(head)
    return f"git -C {worktree} diff $(git -C {worktree} merge-base {base} {head}) {head}"


def headers(decisions):
    """The decisions file's `ci:` and `test_command:` header lines (those above its first `## ` section), when set."""
    try:
        text = Path(decisions).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}
    found = {}
    for line in text.splitlines():
        if line.startswith("## "):
            break
        key, sep, value = line.partition(": ")
        if sep and key in ("ci", "test_command") and value.strip():
            found[key] = value.strip()
    if found.get("ci") not in (None, "on", "off"):
        del found["ci"]
    return found


class Failed(Exception):
    """A command `--remote` runs failed; the message says which and why."""


def output(command, cwd, what):
    """`command`'s stdout, run in `cwd`; `Failed` with `what` and its stderr when it fails."""
    try:
        got = subprocess.run(command, cwd=cwd, capture_output=True, text=True, encoding="utf-8")
    except OSError as e:
        raise Failed(f"{what}: {e}") from e
    if got.returncode != 0:
        raise Failed(f"{what}: {got.stderr.strip() or f'exit {got.returncode}'}")
    return got.stdout


def write_diff(worktree, base, head, path):
    """Writes the diff `diff()` names into `path`, run here, since the remote snapshot has no history."""
    git = ["git", "-C", worktree]
    merge_base = output(git + ["merge-base", base, head], None, f"diff merge-base {base} {head}").strip()
    path.write_text(output(git + ["diff", merge_base, head], None, f"diff {base} {head}"), encoding="utf-8")


class Attachments:
    """The files to attach, by the distinct basename each has in `~/attached`."""

    def __init__(self, folder):
        self.folder = folder
        self.paths = {}

    def add(self, path, option):
        """`path`'s name in `~/attached`; a path whose name is taken is copied into the folder under a free one."""
        if not os.path.isfile(path):
            raise Failed(f"no {option} file: {path}")
        base = name = os.path.basename(path)
        n = 1
        while name in self.paths:
            name = f"{option}{n if n > 1 else ''}-{base}"
            n += 1
        if name != base:
            path = shutil.copyfile(path, os.path.join(self.folder, name))
        self.paths[name] = path
        return f"{ATTACHED}/{name}"

    def write(self):
        Path(self.folder, "attach.txt").write_text("".join(f"{p}\n" for p in self.paths.values()), encoding="utf-8")


def check_author(facts, allowed):
    """`Failed` unless the PR facts' author is one of the `allowed` logins: a remote reviewer runs the PR's tests in a
    sandbox that holds a token, so it reviews only PRs by the human or the run's identity."""
    try:
        login = json.loads(facts)["author"]["login"]
    except (ValueError, KeyError, TypeError):
        raise Failed("author: pr-view names no author; review this PR locally") from None
    if login.casefold() not in {a.casefold() for a in allowed}:
        raise Failed(f"author {login} is not in --allow-author ({', '.join(allowed)}): its tests could read the "
                     "sandbox's token, so review this PR locally")


def files(args):
    """The run's files the inputs name, by input key, local paths; `spec.md` only for a reviewer, when it exists."""
    decisions = args.decisions or (args.run and f"{args.run}/decisions.md")
    named = {"decisions": decisions, "map": args.run and f"{args.run}/map.md", "task": args.task,
             "findings": args.findings}
    if args.mode != "security" and args.run and os.path.isfile(f"{args.run}/spec.md"):
        named["spec"] = f"{args.run}/spec.md"
    return {key: path for key, path in named.items() if path}


def remote_inputs(args, named):
    """The inputs `--remote` names instead of local paths and commands, after writing the diff, the PR facts and the
    attach list into `args.remote`."""
    folder = Path(args.remote)
    folder.mkdir(parents=True, exist_ok=True)
    attach = Attachments(str(folder))
    facts = folder / f"pr-{args.pr}.json"
    text = output(["gh", "pr", "view", str(args.pr), "--json", PR_FIELDS], args.worktree, "pr-view")
    check_author(text, args.allow_author)
    facts.write_text(text, encoding="utf-8")
    inputs = {"worktree": WORK, "pr_facts": attach.add(str(facts), "pr")}
    whole = folder / f"pr-{args.pr}.diff"
    write_diff(args.worktree, args.base, args.head, whole)
    inputs["diff"] = f"cat {attach.add(str(whole), 'diff')}"
    for key, path in named.items():
        inputs[key] = attach.add(path, key)
    attach.write()
    return inputs


def inputs(args):
    """The role and its input object for `args`."""
    named = files(args)
    values = {"worktree": args.worktree, "head": args.head, "base": args.base,
              "diff": diff(args.worktree, args.base, args.head)}
    if args.remote:
        values.update(remote_inputs(args, named))
        values["remote"] = True
    else:
        values.update(named)
    if "decisions" in named:
        values.update(headers(named["decisions"]))
    if args.pr:
        values["pr"] = args.pr
    if args.output:
        values["output"] = args.output
    if args.mode == "security":
        values.pop("ci", None)
        if "map" in values:
            values["checks"] = values.pop("map")
        role, first = "security", {"scope": "pr"}
    else:
        role, first = "reviewer", {"mode": args.mode}
    order = ("pr", "worktree", "head", "base", "diff", "pr_facts", "task", "findings", "decisions", "ci",
             "test_command", "map", "checks", "spec", "output", "remote")
    return role, {**first, **{k: values[k] for k in order if k in values}}


def main(argv):
    parser, args = parse(argv)
    try:
        role, values = inputs(args)
    except Failed as e:
        sys.stderr.write(f"review_prompt.py: {e}\n")
        return 1
    try:
        text = protocol.prompt(role, values)
    except ValueError as e:
        parser.error(f"bad input: {e}")
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
