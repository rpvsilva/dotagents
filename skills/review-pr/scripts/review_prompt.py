#!/usr/bin/env python3
"""Prints a reviewer's prompt for one review mode, from file pointers only: no summary of the change.

Usage: python3 -I review_prompt.py <mode> [--pr <n>] --worktree <path> --head <sha> --base <ref>
         [--run <run>] [--decisions <file>] [--task <file>] [--findings <file>] [--ticket <file>] [--task-base <ref>]
         [--report <file> | --remote <dir> --allow-author <login>...]

Modes: `task` (one task PR; with no `--pr`, a task's local commits before they merge, `--base` its ticket branch and
`--task` required: build-epic's review of a high-tier task, which has no PR), `hub` (section A: the hub task PR against `--task-base`, the ticket branch; section B:
the whole ticket against `--base`; needs `--ticket` and `--task-base`), `ticket` (`--task` is the ticket file),
`re-review` (`--base` is the head last reviewed; needs `--findings`, the numbered earlier findings) and `security`.
Every mode diffs against `git merge-base <base> <head>` and runs tests only after the whole diff is read. With
`--run`, the prompt points at `<run>/decisions.md`, its `ci:` line and `<run>/map.md`'s Checks; without it, at none
of them, so standalone `review-pr` works. `--decisions` names the decisions file instead of `<run>/decisions.md`,
with or without `--run`; the prompt names its `test_command:` line as the command to run the tests with. `--report`
names the file the reviewer writes its report to (a `build-epic` run's `<run>/reviews/<PR>-<sha7>.md`), the only file
it may write; the prompt then ends by saying so. The text comes from `templates/<mode>.md`.

`--remote <dir>` renders the prompt for a reviewer run remotely, on a snapshot of the worktree with no history and no
GitHub access. It writes into `<dir>` the diff (`pr-<n>.diff`; in `hub` mode also the task diff, `pr-<n>-task.diff`)
and the PR facts (`pr-<n>.json`, from `gh pr view <n> --json author,title,body,headRefOid,baseRefName,files`), and
`attach.txt`: the absolute path of each file to attach, one per line (those, the decisions file, `<run>/map.md`,
`<run>/spec.md` when it exists, `--task`, `--ticket` and `--findings`), every basename distinct: an input whose name is
taken is copied into `<dir>` as `<its option>-<name>`. The prompt names the worktree as `~/work` and each input as
`~/attached/<name>`, runs no `gh` or git command, and ends by asking for the report as the final message, since the
reviewer writes no file. A remote reviewer runs the PR's tests in a sandbox holding a token, so `--remote` renders only
a PR whose author is one of the `--allow-author` logins (repeatable, as `gh pr view --json author` spells them; case
aside): the human's and the run's identity. Any other author is refused before the diff is written, and that reviewer
runs locally, reading the whole diff before it runs anything.

Exits 2 with a usage line on an unknown mode, a missing option (`--pr` in any review but a local `task` one), a `--findings` file that does not exist, or
`--remote` with `--report` or without `--allow-author`; with `--remote`, exits 1 with a reason on stderr when
`gh pr view`, the merge base or the diff fails, the PR's author is not allowed, or an input to attach is missing.
"""
import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from string import Template

TEMPLATES = Path(__file__).resolve().parent / "templates"
BRIEFS = "~/.agents/skills"
REVIEWER_BRIEF = f"{BRIEFS}/review-pr/reviewer-brief.md"
SECURITY_BRIEF = f"{BRIEFS}/security-audit/security-brief.md"
MODES = ("task", "hub", "ticket", "re-review", "security")
PR_FIELDS = "author,title,body,headRefOid,baseRefName,files"
WORK = "~/work"
ATTACHED = "~/attached"
REMOTE = ("\nYou run remotely: {work} is a snapshot of the PR head, one commit with no history, no other branches and no "
          "GitHub access, so skip the brief's test run on the merge base; {attached} holds your inputs, read-only. The "
          "PR's title, description, base branch and changed files are in {facts}: read it for what the PR claims to "
          "do. Change no file. Your final message is your whole report, ending with the brief's block.\n")


def absolute(path):
    """`path` made absolute against the caller's folder, since the reviewer runs elsewhere."""
    return os.path.abspath(os.path.expanduser(path))


def parse(argv):
    p = argparse.ArgumentParser(prog="review_prompt.py")
    p.add_argument("mode", choices=MODES)
    p.add_argument("--pr")
    p.add_argument("--worktree", required=True, type=absolute)
    p.add_argument("--head", required=True)
    p.add_argument("--base", required=True)
    p.add_argument("--run", type=absolute)
    p.add_argument("--decisions", type=absolute)
    p.add_argument("--task", type=absolute)
    p.add_argument("--findings", type=absolute)
    p.add_argument("--ticket", type=absolute)
    p.add_argument("--task-base")
    p.add_argument("--report", type=absolute)
    p.add_argument("--remote", type=absolute)
    p.add_argument("--allow-author", action="append", default=[])
    args = p.parse_args(argv)
    if args.remote and args.report:
        p.error("--remote takes no --report: a remote reviewer returns its report as its final message")
    if args.remote and not args.allow_author:
        p.error("--remote needs --allow-author: only a PR by the human or the run's identity is reviewed remotely")
    if not args.pr and (args.mode != "task" or not args.task or args.remote):
        p.error("--pr is required, except in a local task review with --task")
    if args.mode == "re-review" and not args.findings:
        p.error("re-review needs --findings")
    if args.findings and not os.path.isfile(args.findings):
        p.error(f"no findings file: {args.findings}")
    if args.mode == "hub" and not (args.ticket and args.task_base):
        p.error("hub needs --ticket and --task-base")
    return args


def diff(worktree, base, head):
    """The command for the diff to review: from the merge base, so changes on the base since are not in it. Each value
    is quoted, since the reviewer runs the command in a shell."""
    worktree, base, head = shlex.quote(worktree), shlex.quote(base), shlex.quote(head)
    return f"git -C {worktree} diff $(git -C {worktree} merge-base {base} {head}) {head}"


def run_reads(decisions, map_md):
    """The run folder's lines of the read list: the decisions file and its `ci:` line, then the run's Checks in
    `map_md`; none of them without a run folder or decisions file."""
    reads = []
    if decisions:
        reads += [f"- {decisions}: a rule it waives is never a finding",
                  "- its `ci:` line: with `ci: off`, missing CI checks are not a finding",
                  "- its `test_command:` line: the exact command to run the tests with"]
    if map_md:
        reads.append(f"- {map_md}, its `Checks` section: the commands to run tests with")
    else:
        reads.append("- the test commands the repo's rule files name")
    return reads


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


def remote_inputs(args):
    """The values `--remote` names instead of local paths and commands, after writing the diffs, the PR facts and the
    attach list into `args.remote`."""
    folder = Path(args.remote)
    folder.mkdir(parents=True, exist_ok=True)
    attach = Attachments(str(folder))
    facts = folder / f"pr-{args.pr}.json"
    text = output(["gh", "pr", "view", args.pr, "--json", PR_FIELDS], args.worktree, "pr-view")
    check_author(text, args.allow_author)
    facts.write_text(text, encoding="utf-8")
    inputs = {"facts": attach.add(str(facts), "pr")}
    whole = folder / f"pr-{args.pr}.diff"
    write_diff(args.worktree, args.base, args.head, whole)
    inputs["diff"] = f"cat {attach.add(str(whole), 'diff')}"
    if args.task_base:
        task = folder / f"pr-{args.pr}-task.diff"
        write_diff(args.worktree, args.task_base, args.head, task)
        inputs["task_diff"] = f"cat {attach.add(str(task), 'diff')}"
    decisions = args.decisions or (args.run and f"{args.run}/decisions.md")
    inputs["decisions"] = decisions and attach.add(decisions, "decisions")
    inputs["map"] = args.run and attach.add(f"{args.run}/map.md", "map")
    if args.run and os.path.isfile(f"{args.run}/spec.md"):
        inputs["spec"] = attach.add(f"{args.run}/spec.md", "spec")
    for option in ("task", "ticket", "findings"):
        path = getattr(args, option)
        inputs[option] = path and attach.add(path, option)
    attach.write()
    return inputs


def render(args):
    remote = remote_inputs(args) if args.remote else None
    brief = SECURITY_BRIEF if args.mode == "security" else REVIEWER_BRIEF
    if remote:
        reads = [f"- {brief}: follow it", *run_reads(remote["decisions"], remote["map"])]
        if remote.get("spec"):
            reads.append(f"- {remote['spec']}: the behaviours the run promises")
        task, ticket, findings, worktree = remote["task"], remote["ticket"], remote["findings"], WORK
    else:
        decisions = args.decisions or (args.run and f"{args.run}/decisions.md")
        reads = [f"- {brief}: follow it", *run_reads(decisions, args.run and f"{args.run}/map.md")]
        task, ticket, findings, worktree = args.task, args.ticket, args.findings, args.worktree
    if task and args.mode != "hub":
        reads.append(f"- {task}: what the change was asked to do")
    subject = (f"task PR #{args.pr}. The worktree {worktree} is checked out at its head, {args.head}" if args.pr else
               f"the commits of task {task}, before they merge into {args.base}. There is no PR: read what the task is "
               f"for in its file, not `gh pr view`. The worktree {worktree} is at the task's head, {args.head}")
    values = {
        "pr": args.pr,
        "subject": subject,
        "worktree": worktree,
        "head": args.head,
        "base": args.base,
        "reads": "\n".join(reads),
        "diff": remote["diff"] if remote else diff(args.worktree, args.base, args.head),
        "findings": findings or "",
        "task": task or "",
        "ticket": ticket or "",
        "task_diff": "",
    }
    if args.task_base:
        values["task_diff"] = remote["task_diff"] if remote else diff(args.worktree, args.task_base, args.head)
    if args.mode == "hub" and not task:
        values["task"] = (f"the PR description, in {remote['facts']}" if remote
                          else f"the PR description (`gh pr view {args.pr}`)")
    prompt = Template((TEMPLATES / f"{args.mode}.md").read_text(encoding="utf-8")).substitute(values)
    if remote:
        prompt += REMOTE.format(work=WORK, attached=ATTACHED, facts=remote["facts"])
    if args.report:
        prompt += (f"\nWrite your report to {args.report}, the only file you may write, then return the block alone, "
                   "as the brief's Report says.\n")
    return prompt


def main(argv):
    try:
        prompt = render(parse(argv))
    except Failed as e:
        sys.stderr.write(f"review_prompt.py: {e}\n")
        return 1
    sys.stdout.write(prompt)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
