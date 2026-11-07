#!/usr/bin/env python3
"""Prints a reviewer's prompt for one review mode, from file pointers only: no summary of the change.

Usage: python3 -I review_prompt.py <mode> --pr <n> --worktree <path> --head <sha> --base <ref>
         [--run <run>] [--decisions <file>] [--task <file>] [--findings <file>] [--ticket <file>] [--task-base <ref>]

Modes: `task` (one task PR), `hub` (section A: the hub task PR against `--task-base`, the ticket branch; section B:
the whole ticket against `--base`; needs `--ticket` and `--task-base`), `ticket` (`--task` is the ticket file),
`re-review` (`--base` is the head last reviewed; needs `--findings`, the numbered earlier findings) and `security`.
Every mode diffs against `git merge-base <base> <head>` and runs tests only after the whole diff is read. With
`--run`, the prompt points at `<run>/decisions.md`, its `ci:` line and `<run>/map.md`'s Checks; without it, at none
of them, so standalone `review-pr` works. `--decisions` names the decisions file instead of `<run>/decisions.md`,
with or without `--run`. The text comes from `templates/<mode>.md`.

Exits 2 with a usage line on an unknown mode, a missing option, or a `--findings` file that does not exist.
"""
import argparse
import os
import shlex
import sys
from pathlib import Path
from string import Template

TEMPLATES = Path(__file__).resolve().parent / "templates"
BRIEFS = "~/.agents/skills"
REVIEWER_BRIEF = f"{BRIEFS}/review-pr/reviewer-brief.md"
SECURITY_BRIEF = f"{BRIEFS}/security-audit/security-brief.md"
MODES = ("task", "hub", "ticket", "re-review", "security")


def absolute(path):
    """`path` made absolute against the caller's folder, since the reviewer runs elsewhere."""
    return os.path.abspath(os.path.expanduser(path))


def parse(argv):
    p = argparse.ArgumentParser(prog="review_prompt.py")
    p.add_argument("mode", choices=MODES)
    p.add_argument("--pr", required=True)
    p.add_argument("--worktree", required=True, type=absolute)
    p.add_argument("--head", required=True)
    p.add_argument("--base", required=True)
    p.add_argument("--run", type=absolute)
    p.add_argument("--decisions", type=absolute)
    p.add_argument("--task", type=absolute)
    p.add_argument("--findings", type=absolute)
    p.add_argument("--ticket", type=absolute)
    p.add_argument("--task-base")
    args = p.parse_args(argv)
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


def run_reads(run, decisions=None):
    """The run folder's lines of the read list: the decisions file (`decisions`, else the run's) and its `ci:` line,
    then the run's Checks; none of them without a run folder or decisions file."""
    decisions = decisions or (run and f"{run}/decisions.md")
    reads = []
    if decisions:
        reads += [f"- {decisions}: a rule it waives is never a finding",
                  "- its `ci:` line: with `ci: off`, missing CI checks are not a finding"]
    if run:
        reads.append(f"- {run}/map.md, its `Checks` section: the commands to run tests with")
    else:
        reads.append("- the test commands the repo's rule files name")
    return reads


def render(args):
    brief = SECURITY_BRIEF if args.mode == "security" else REVIEWER_BRIEF
    reads = [f"- {brief}: follow it", *run_reads(args.run, args.decisions)]
    if args.task and args.mode != "hub":
        reads.append(f"- {args.task}: what the change was asked to do")
    values = {
        "pr": args.pr,
        "worktree": args.worktree,
        "head": args.head,
        "base": args.base,
        "reads": "\n".join(reads),
        "diff": diff(args.worktree, args.base, args.head),
        "findings": args.findings or "",
        "task": args.task or "",
        "ticket": args.ticket or "",
        "task_diff": diff(args.worktree, args.task_base, args.head) if args.task_base else "",
    }
    if args.mode == "hub" and not args.task:
        values["task"] = f"the PR description (`gh pr view {args.pr}`)"
    return Template((TEMPLATES / f"{args.mode}.md").read_text()).substitute(values)


def main(argv):
    sys.stdout.write(render(parse(argv)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
