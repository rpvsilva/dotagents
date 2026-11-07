#!/usr/bin/env python3
"""Pins the remote dispatcher when the epic changes the repo it is installed from.

Usage: python3 -I pin_dispatcher.py <run>
Compares the real path of `~/.agents/bin/remote-agent` with the real path of `scripts/remote-agent.sh` in the run's
repo. When the link is missing or points elsewhere, nothing is pinned: prints `OK dispatcher installed`, and the
orchestrator dispatches with `~/.agents/bin/remote-agent`. When they match, it writes the tip of
`origin/<default>` into `<run>/dispatcher.sha` once per run and keeps `<run>/worktrees/dispatcher` detached at that
commit (re-created when gone, never moved to a newer one): prints `OK dispatcher pinned <sha7>`, and the orchestrator
dispatches with `<run>/worktrees/dispatcher/scripts/remote-agent.sh`. An existing `dispatcher.sha` is reused, and
keeps the run pinned.
"""
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from steplib import core, gitops, runfolder  # noqa: E402

SHA = re.compile(r"^[0-9a-f]{40}([0-9a-f]{24})?$")


def step(ctx):
    if ctx.args:
        raise core.Fail("usage: pin_dispatcher.py <run>")
    info = runfolder.run_info(ctx.run_dir)
    sha_file = ctx.run_dir / "dispatcher.sha"
    if not sha_file.exists():
        link = Path(core.home()) / ".agents" / "bin" / "remote-agent"
        ours = Path(info["Repo"]) / "scripts" / "remote-agent.sh"
        ctx.note(f"dispatcher {link} -> {core.resolve(link)}; repo's {core.resolve(ours)}")
        if not link.exists() or not ours.exists() or core.resolve(link) != core.resolve(ours):
            return "installed"
    git = gitops.Git(info["Repo"], ctx.log)
    if sha_file.exists():
        sha = sha_file.read_text().strip()
        if not SHA.match(sha):
            raise core.Fail(f"bad-dispatcher-sha: {sha_file}")
    else:
        git.fetch()
        sha = git.tip(f"refs/remotes/origin/{info['Default']}")
        tmp = sha_file.with_name(".dispatcher.sha.tmp")
        tmp.write_text(sha + "\n")
        os.replace(str(tmp), str(sha_file))
    wt = ctx.run_dir / "worktrees" / "dispatcher"
    if wt.exists():
        head = ctx.run(["git", "-C", wt, "rev-parse", "HEAD"])
        if head.rc != 0 or head.out.strip() != sha:
            raise core.Fail(f"dispatcher-moved: {wt} is not at {sha[:7]}")
    else:
        wt.parent.mkdir(parents=True, exist_ok=True)
        with gitops.lock(info["Repo"]):  # a folder deleted by hand stays registered until pruned
            ctx.run(["git", "-C", info["Repo"], "worktree", "prune"])
        git.worktree_add(wt, sha, detach=True)
    return "pinned", sha[:7]


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.stdout.write("FAIL dispatcher usage: pin_dispatcher.py <run>\n")
        sys.exit(2)
    # `dispatcher` is the id: the log is `<run>/logs/dispatcher-pin_dispatcher.log`.
    core.main("pin_dispatcher", step, argv=[sys.argv[1], "dispatcher", *sys.argv[2:]])
