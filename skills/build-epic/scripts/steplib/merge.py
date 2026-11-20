"""Merges a PR by the repo's merge rule and reads its state back, so a merge that did not happen is never reported done.

The rule is the `merge_command` Setting (e.g. `scripts/merge-pr.sh {pr}`, run from the repo root with `{pr}` and
`{sha}` filled in) or, when it is unset, `gh pr merge <n> --squash --delete-branch --match-head-commit <sha>`.
Before merging, the PR's head must be the pushed `sha` (a fresh push can take a few seconds to show); after, the PR
must read `MERGED` at that head. Every merge first proves who is merging (`identity.py`).

A PR merges only into the base its caller expects (`check_base`): `ship_ticket.py` names the run's base (`ci.base`);
any other base fails `wrong-base <branch>` and nothing merges. Tasks have no PR: they merge locally (`merge_task.py`).

Fails `wrong-base <branch>`, `wrong-identity`, `head-moved` or `merge`; a `merge` failure carries the
rule's own output in its `said` attribute, so a caller can read the rule's answer and nothing else.
"""
import json
import os
import re
import time

from . import core, corecmd, gh, identity

HEAD_TRIES = 6
HEAD_WAIT = 5


def setting(repo, key, log, section="build-epic"):
    """The effective value of Setting `key` for `repo`, through the `settings` command; None when unset."""
    tool = corecmd.path("settings")
    if not os.path.exists(tool):
        raise core.Fail(f"no-settings: {tool} is missing; run install.sh")
    res = core.run([tool, "--dir", repo, "get", section, key], log)
    if res.rc != 0:
        raise core.Fail(f"setting-{key}")
    return res.out.strip() or None


def view(pr, log, repo):
    """The PR's `state`, `headRefOid` and `baseRefName`, as a dict."""
    res = gh.gh(["pr", "view", str(pr), "--json", "state,headRefOid,baseRefName"], log, cwd=repo)
    try:
        got = json.loads(res.out) if res.rc == 0 else None
    except ValueError:
        got = None
    if not isinstance(got, dict):
        raise core.Fail("pr-view")
    return got


def check_base(pr, into, expected, log):
    """Fails `wrong-base <into>` unless PR `pr`'s base `into` is `expected`."""
    if into != expected:
        log.write(f"PR #{pr} is into {into}; it may merge only into {expected}\n")
        raise core.Fail(f"wrong-base {into or '?'}")


def merge_argv(command, pr, sha):
    """The argv of the merge rule for `pr` at `sha`. A command naming neither `{pr}` nor `{sha}` gets the PR appended."""
    if not command:
        return ["gh", "pr", "merge", pr, "--squash", "--delete-branch", "--match-head-commit", sha]
    words = core.split(command)
    argv = [w.replace("{pr}", pr).replace("{sha}", sha) for w in words]
    if not any("{pr}" in w or "{sha}" in w for w in words):
        argv.append(pr)
    return argv


def merge(pr, sha, repo, log, base, command=None, run=None):
    """Merges PR `pr` (a number) at head `sha` into `base`, the only base it accepts, by the rule, from `repo`'s root,
    as the identity `run` (default: the step's run folder) decides. Returns once it reads `MERGED`."""
    run = run or core.RUN_DIR
    pr, scale = str(pr), float(os.environ.get("BUILD_EPIC_RETRY_SLEEP", "1"))
    got = view(pr, log, repo)
    check_base(pr, got.get("baseRefName"), base, log)
    argv = merge_argv(command, pr, sha)
    who = identity.mode(run, lambda: setting(repo, "merge_identity", log))
    env = identity.prove(run, who, repo, log)
    for attempt in range(HEAD_TRIES):
        head = (got if attempt == 0 else view(pr, log, repo)).get("headRefOid")
        if head == sha:
            break
        log.write(f"PR #{pr} head is {head}, not {sha} yet\n")
        if attempt < HEAD_TRIES - 1:
            time.sleep(HEAD_WAIT * scale)
    else:
        log.write(f"PR #{pr} head stayed {head}; pushed {sha}\n")
        raise core.Fail("head-moved")
    # The rule runs as the proven identity too: its own `gh` calls read `GH_TOKEN`, which `human` leaves unset.
    if argv[0] == "gh":
        res = gh.retry(argv, log, cwd=repo, identity=env is None, env=env)
    else:
        res = core.run(argv, log, cwd=repo, env=gh.identity_env(log) if env is None else env)
    if res.rc != 0 and re.search(r"match-head-commit|Head branch was modified|expected head sha",
                                 res.out + res.err, re.IGNORECASE):
        raise core.Fail("head-moved")
    # Read back even after a non-zero exit: `gh pr merge` exits 1 when it merged but could not delete a local branch
    # still checked out in a worktree. The PR's state is the answer either way.
    got = view(pr, log, repo)
    log.write(f"PR #{pr} reads {got.get('state')} at {got.get('headRefOid')}; merge rule exited {res.rc}\n")
    if got.get("state") != "MERGED":
        fail = core.Fail("merge")
        fail.said = res.out + res.err
        raise fail
    if got.get("headRefOid") != sha:
        raise core.Fail("head-moved")
