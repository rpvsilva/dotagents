"""Merges a PR by the repo's merge rule and reads its state back, so a merge that did not happen is never reported done.

The rule is the `merge_command` Setting (e.g. `scripts/merge-pr.sh {pr}`, run from the repo root with `{pr}` and
`{sha}` filled in) or, when it is unset, `gh pr merge <n> --squash --delete-branch --match-head-commit <sha>`.
Before merging, the PR's head must be the pushed `sha` (a fresh push can take a few seconds to show); after, the PR
must read `MERGED` at that head. Fails `head-moved` or `merge`.
"""
import json
import os
import re
import shlex
import time

from . import core, gh

HEAD_TRIES = 6
HEAD_WAIT = 5


def setting(repo, key, log, section="build-epic"):
    """The effective value of Setting `key` for `repo`, through `~/.agents/bin/settings`; None when unset."""
    tool = os.path.expanduser("~/.agents/bin/settings")
    if not os.path.exists(tool):
        raise core.Fail(f"no-settings: {tool} is missing; run install.sh")
    res = core.run([tool, "--dir", repo, "get", section, key], log)
    if res.rc != 0:
        raise core.Fail(f"setting-{key}")
    return res.out.strip() or None


def view(pr, log, repo):
    """The PR's `state` and `headRefOid`, as a dict."""
    res = gh.gh(["pr", "view", pr, "--json", "state,headRefOid"], log, cwd=repo)
    try:
        got = json.loads(res.out) if res.rc == 0 else None
    except ValueError:
        got = None
    if not isinstance(got, dict):
        raise core.Fail("pr-view")
    return got


def merge_argv(command, pr, sha):
    """The argv of the merge rule for `pr` at `sha`. A command naming neither `{pr}` nor `{sha}` gets the PR appended."""
    if not command:
        return ["gh", "pr", "merge", pr, "--squash", "--delete-branch", "--match-head-commit", sha]
    words = shlex.split(command)
    argv = [w.replace("{pr}", pr).replace("{sha}", sha) for w in words]
    if not any("{pr}" in w or "{sha}" in w for w in words):
        argv.append(pr)
    return argv


def merge(pr, sha, repo, log, command=None):
    """Merges PR `pr` (a number) at head `sha` by the rule, from `repo`'s root. Returns once it reads `MERGED`."""
    pr, scale = str(pr), float(os.environ.get("BUILD_EPIC_RETRY_SLEEP", "1"))
    for attempt in range(HEAD_TRIES):
        head = view(pr, log, repo).get("headRefOid")
        if head == sha:
            break
        log.write(f"PR #{pr} head is {head}, not {sha} yet\n")
        if attempt < HEAD_TRIES - 1:
            time.sleep(HEAD_WAIT * scale)
    else:
        log.write(f"PR #{pr} head stayed {head}; pushed {sha}\n")
        raise core.Fail("head-moved")
    argv = merge_argv(command, pr, sha)
    res = gh.retry(argv, log, cwd=repo) if argv[0] == "gh" else core.run(argv, log, cwd=repo)
    if res.rc != 0 and re.search(r"match-head-commit|Head branch was modified|expected head sha",
                                 res.out + res.err, re.IGNORECASE):
        raise core.Fail("head-moved")
    # Read back even after a non-zero exit: `gh pr merge` exits 1 when it merged but could not delete a local branch
    # still checked out in a worktree. The PR's state is the answer either way.
    got = view(pr, log, repo)
    log.write(f"PR #{pr} reads {got.get('state')} at {got.get('headRefOid')}; merge rule exited {res.rc}\n")
    if got.get("state") != "MERGED":
        raise core.Fail("merge")
    if got.get("headRefOid") != sha:
        raise core.Fail("head-moved")
