#!/usr/bin/env python3
"""Records the run's repo, its default branch, its remote and its identity in `<run>/run.md`, so later steps never
read the current folder.

Usage: python3 -I init_run.py <run> <repo> [--epic <n>]
Prints `OK run initialised repo=<path> default=<branch> remote=<url> identity=<command|login>`. Rerunning updates the
lines. The identity is the `identity_command` Setting, a command printing a GitHub token (e.g.
`scripts/gh-app-token.sh`) that every later `gh` call gets a fresh token from (`steplib/gh.py`); unset: `login`, the
current `gh` login. `--epic` records the epic's issue for `tracker_cmd.py close-epic`.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from steplib import core, gh, runfolder, tracker  # noqa: E402

USAGE = "usage: init_run.py <run> <repo> [--epic <n>]"


def step(ctx):
    args, epic = list(ctx.args), None
    if "--epic" in args:
        at = args.index("--epic")
        if at + 1 >= len(args) or not args[at + 1].lstrip("#").isdigit():
            raise core.Fail(USAGE)
        epic = args[at + 1].lstrip("#")
        del args[at:at + 2]
    if len(args) != 1:
        raise core.Fail(USAGE)
    given = core.resolve(args[0])
    top = ctx.run(["git", "-C", given, "rev-parse", "--show-toplevel"])
    if top.rc != 0:
        raise core.Fail(f"not-a-repo: {given}")
    repo = core.resolve(top.out.strip())
    remote = ctx.run(["git", "-C", repo, "remote", "get-url", "origin"])
    if remote.rc != 0:
        raise core.Fail("no-origin")
    head = ctx.run(["git", "-C", repo, "symbolic-ref", "--short", "refs/remotes/origin/HEAD"])
    default = head.out.strip().split("/", 1)[-1] if head.rc == 0 else ""
    if not default:
        ls = ctx.run(["git", "-C", repo, "ls-remote", "--symref", "origin", "HEAD"])
        for line in ls.out.splitlines() if ls.rc == 0 else ():
            if line.startswith("ref: refs/heads/") and line.endswith("\tHEAD"):
                default = line[len("ref: refs/heads/"):-len("\tHEAD")]
    if not default:
        raise core.Fail("no-default-branch")
    run_md = ctx.run_dir / "run.md"
    ctx.run_dir.mkdir(parents=True, exist_ok=True)
    who = tracker.setting("build-epic", "identity_command", str(repo), ctx.log) or gh.LOGIN
    lines = [("Repo", repo), ("Default", default), ("Remote", remote.out.strip()), ("Identity", who)]
    for key, value in lines + ([("Epic", f"#{epic}")] if epic else []):
        runfolder.set_header(run_md, key, value)
    return "initialised", f"repo={repo} default={default} remote={remote.out.strip()} identity={who}"


if __name__ == "__main__":
    core.main("init_run", step, takes_id=False, usage="<repo>")
