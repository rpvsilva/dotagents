#!/usr/bin/env python3
"""Records the run's repo, its default branch and its remote in `<run>/run.md`, so later steps never read the
current folder.

Usage: python3 -I init_run.py <run> <repo>
Prints `OK run initialised repo=<path> default=<branch> remote=<url>`. Rerunning updates the three lines.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from steplib import core, runfolder  # noqa: E402


def step(ctx):
    if len(ctx.args) != 1:
        raise core.Fail("usage: init_run.py <run> <repo>")
    given = core.resolve(ctx.args[0])
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
    for key, value in (("Repo", repo), ("Default", default), ("Remote", remote.out.strip())):
        runfolder.set_header(run_md, key, value)
    return "initialised", f"repo={repo} default={default} remote={remote.out.strip()}"


if __name__ == "__main__":
    core.main("init_run", step, takes_id=False, usage="<repo>")
