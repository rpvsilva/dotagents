"""Ticket CI: the draft ticket PR into the run's base, and its checks read before each later task merge.

The base is `decisions.md`'s `base:`, or the default branch when it is unset (`base`). The ticket's draft PR is where
a ticket's merged tasks meet CI.
`decisions.md`'s `ci: off` (or `none`) turns all of it off. A red check is reported once per failing run: the
orchestrator hands it to a later task, so the next merge into the ticket goes ahead on the same red run.
"""
import json
import os
import re
import time

from . import core, gh, runfolder

POLL_WAIT = 30
POLLS = 120  # an hour of pending checks, then `ticket-ci-timeout`
CONVENTIONAL = re.compile(r"^[a-z]+(\([^)]*\))?!?: ")


def enabled(run):
    """False when `<run>/decisions.md` says `ci: off` or `ci: none`; True otherwise (no file: CI assumed on)."""
    path = core.resolve(run) / "decisions.md"
    value = runfolder.get(path, "ci") if path.exists() else None
    return (value or "on").lower() not in ("off", "none", "no")


def base(run, default=None):
    """The branch the run's tickets are cut from and open against: `<run>/decisions.md`'s `base:` when it names a
    branch, else `default` (None: `run.md`'s `Default:`)."""
    path = core.resolve(run) / "decisions.md"
    value = (runfolder.get(path, "base") if path.exists() else None) or ""
    value = value.strip().strip("`")
    if value.lower() not in ("", "null", "none", "default", "~"):
        return value
    return default or runfolder.run_info(run)["Default"]


def pr_number(value):
    """The PR number in a `PR:` line's value (`#12`, `12` or a PR URL), or None."""
    m = re.search(r"(\d+)\D*$", value or "")
    return m.group(1) if m else None


def checks(pr, log, repo):
    """The ticket PR's checks as dicts with `name`, `bucket` and `link`, waiting while any is pending."""
    scale = float(os.environ.get("BUILD_EPIC_RETRY_SLEEP", "1"))
    for poll in range(POLLS):
        res = gh.gh(["pr", "checks", pr, "--json", "name,bucket,link"], log, cwd=repo)
        if "no checks reported" in res.err + res.out:
            return []
        try:
            got = json.loads(res.out)
        except ValueError:
            raise core.Fail("ticket-ci")
        if not any(c.get("bucket") == "pending" for c in got):
            return got
        log.write(f"ticket PR #{pr}: checks pending, poll {poll + 1}/{POLLS}\n")
        log.flush()
        time.sleep(POLL_WAIT * scale)
    raise core.Fail("ticket-ci-timeout")


def gate(run, ticket, log, repo):
    """Before a task merge: when the ticket has a PR, waits on its pending checks and fails `ticket-ci-red <run-id>`
    on a red one not reported before. Cancelled runs are ignored."""
    pr = pr_number(runfolder.get(ticket, "PR"))
    if not pr or not enabled(run):
        return
    red = [c for c in checks(pr, log, repo) if c.get("bucket") == "fail"]
    if not red:
        return
    m = re.search(r"/actions/runs/(\d+)", red[0].get("link") or "")
    run_id = m.group(1) if m else re.sub(r"\s+", "-", red[0].get("name") or "unknown")
    if runfolder.get(ticket, "CI red") == run_id:
        log.write(f"ticket CI run {run_id} is red, already reported; merging on\n")
        return
    if m:
        gh.gh(["run", "view", run_id, "--log-failed"], log, cwd=repo)
    runfolder.set_header(ticket, "CI red", run_id)
    raise core.Fail(f"ticket-ci-red {run_id}")


def title(ticket):
    """The ticket PR's title: the ticket's title, in the commit convention (`feat: ` unless it already has a type).
    The title's own case is kept."""
    m = re.search(r"^# *[^:\n]*: *(.+)$", ticket.read_text(encoding="utf-8"), re.MULTILINE)
    text = m.group(1).strip() if m else ticket.stem
    return text if CONVENTIONAL.match(text) else f"feat: {text}"


def open_draft(ticket, default, log, repo):
    """After a task merge: opens the draft ticket PR into the run's base (`base`, `default` when unset) when the
    ticket has no `PR:` line (reusing an open one for its branch), and records it. Returns the PR number, or None when
    the ticket already had one."""
    if runfolder.get(ticket, "PR"):
        return None
    default = base(ticket.parent.parent, default)
    branch = runfolder.get(ticket, "Branch")
    if not branch:
        raise core.Fail("ticket-no-branch")
    found = gh.gh(["pr", "list", "--head", branch, "--base", default, "--state", "open", "--json", "number,url"],
                  log, cwd=repo)
    try:
        existing = json.loads(found.out) if found.rc == 0 else None
    except ValueError:
        existing = None
    if existing is None:
        raise core.Fail("ticket-pr")
    if existing:
        url = existing[0].get("url") or f"#{existing[0]['number']}"
    else:
        issues = re.findall(r"#\d+", runfolder.get(ticket, "Issue") or "")
        body = "\n".join(f"Part of {i}" for i in issues) or f"Ticket {ticket.stem} of a build-epic run."
        name = title(ticket)
        argv = ["pr", "create", "--draft", "--base", default, "--head", branch, "--title", name, "--body", body]
        res = gh.gh(argv, log, cwd=repo)
        if res.rc != 0 and re.search(r"draft", res.err, re.IGNORECASE):
            # No draft PRs on this host: a WIP: title stands in, dropped when the ticket ships.
            argv = ["pr", "create", "--base", default, "--head", branch, "--title", f"WIP: {name}", "--body", body]
            res = gh.gh(argv, log, cwd=repo)
        urls = re.findall(r"https?://\S+", res.out)
        if res.rc != 0 or not urls:
            raise core.Fail("ticket-pr")
        url = urls[-1]
    runfolder.set_header(ticket, "PR", url)
    return pr_number(url)
