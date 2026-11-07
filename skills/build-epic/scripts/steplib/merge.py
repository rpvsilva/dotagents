"""Merges a PR by the repo's merge rule and reads its state back, so a merge that did not happen is never reported done.

The rule is the `merge_command` Setting (e.g. `scripts/merge-pr.sh {pr}`, run from the repo root with `{pr}` and
`{sha}` filled in) or, when it is unset, `gh pr merge <n> --squash --delete-branch --match-head-commit <sha>`.
Before merging, the PR's head must be the pushed `sha` (a fresh push can take a few seconds to show); after, the PR
must read `MERGED` at that head. Every merge first proves who is merging (`identity.py`).

A PR merges only into the run's base (`decisions.md`'s `base:`, else the run's default branch) or one of the run's
ticket branches (their `Branch:` lines); any other base fails `wrong-base <branch>` and nothing merges. A task PR (one
into a ticket branch) merges by the `task_merge_method` Setting: `squash`, or `merge` for merge commits, which the
default rule does with `--merge` and a `merge-pr.sh` rule with its `--merge` option; any other rule fails
`merge-method` rather than squash. A PR into the base merges by the rule as it is.

Fails `wrong-base <branch>`, `merge-method`, `wrong-identity`, `head-moved` or `merge`; a `merge` failure carries the
rule's own output in its `said` attribute, so a caller can read the rule's answer and nothing else.
"""
import json
import os
import re
import time

from . import core, decisions, gh, identity, runfolder

HEAD_TRIES = 6
HEAD_WAIT = 5


def setting(repo, key, log, section="build-epic"):
    """The effective value of Setting `key` for `repo`, through `~/.agents/bin/settings`; None when unset."""
    tool = os.path.join(core.home(), ".agents", "bin", "settings")
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


def run_base(run):
    """The branch the run ships into: `decisions.md`'s `base:` when it names one, else `run.md`'s default branch."""
    found = decisions.load(run)
    value = ((found.get("base") if found else None) or "").strip().strip("`")
    return runfolder.run_info(run)["Default"] if value.lower() in ("", "null", "none", "default", "~") else value


def ticket_branches(run):
    """The `Branch:` of every ticket in `<run>/tickets/`."""
    folder = core.resolve(run) / "tickets"
    found = (runfolder.get(f, "Branch") for f in sorted(folder.glob("*.md"))) if folder.is_dir() else ()
    return {b.strip().strip("`") for b in found if b and b.strip().strip("`")}


def merge_argv(command, pr, sha, method="squash"):
    """The argv of the merge rule for `pr` at `sha`. A command naming neither `{pr}` nor `{sha}` gets the PR appended.
    With `method` `merge`, the default rule merges with `--merge` and a `merge-pr.sh` rule gets `--merge` first; any
    other rule fails `merge-method`, since it may squash."""
    if not command:
        return ["gh", "pr", "merge", pr, f"--{method}", "--delete-branch", "--match-head-commit", sha]
    words = core.split(command)
    if method != "squash":
        if os.path.basename(words[0]) != "merge-pr.sh":
            raise core.Fail("merge-method")
        words = [words[0], f"--{method}", *words[1:]]
    argv = [w.replace("{pr}", pr).replace("{sha}", sha) for w in words]
    if not any("{pr}" in w or "{sha}" in w for w in words):
        argv.append(pr)
    return argv


def merge(pr, sha, repo, log, command=None, run=None):
    """Merges PR `pr` (a number) at head `sha` by the rule, from `repo`'s root, as the identity `run` (default: the
    step's run folder) decides. Returns once it reads `MERGED`."""
    run = run or core.RUN_DIR
    pr, scale = str(pr), float(os.environ.get("BUILD_EPIC_RETRY_SLEEP", "1"))
    got = view(pr, log, repo)
    into, base, tickets = got.get("baseRefName"), run_base(run), ticket_branches(run)
    if into != base and into not in tickets:
        log.write(f"PR #{pr} is into {into}; the run merges into {base} or a ticket branch: {' '.join(sorted(tickets))}\n")
        raise core.Fail(f"wrong-base {into or '?'}")
    method = (setting(repo, "task_merge_method", log) or "squash") if into in tickets and into != base else "squash"
    argv = merge_argv(command, pr, sha, method)
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
