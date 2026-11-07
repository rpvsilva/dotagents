#!/usr/bin/env python3
"""Posts a reviewer's report as one COMMENT review on a PR's head commit. It never approves.

Usage: python3 -I post_review.py <run> <PR> <report-file> [--comments <json>]

`<PR>` is a number, `#n` or a PR URL. The report path is resolved against the caller's folder before anything else
runs, so a relative path posts the file's text. `--comments` is a JSON array of review comments (`path`, `line`,
`body`), inline or in a file. The payload `{commit_id, event: COMMENT, body[, comments]}` is kept in
`<run>/logs/PR<n>-review.json` and posted with `gh api repos/{owner}/{repo}/pulls/<n>/reviews`.

Prints `OK PR<n> review <url>`, or `FAIL PR<n> <reason> (log: ...)`: `empty-review` (the file is missing or blank),
`bad-comments`, `pr-view`, `post`, or `post-denied <report-file>` when GitHub refuses the review (HTTP 403 other than
a rate limit, never retried; a rate-limit 403 is a plain `post` failure, so it can be retried): the report stays where it is, so the run's report can name it.
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from steplib import core, gh, runfolder  # noqa: E402

DENIED = re.compile(r"HTTP 403|returned error: 403")
RATE_LIMIT = re.compile(r"rate limit", re.IGNORECASE)
USAGE = "post_review.py <run> <PR> <report-file> [--comments <json>]"


def parse(args):
    """(report path, comments) from the arguments after the PR, the report path already resolved."""
    report, comments, rest = None, None, list(args)
    while rest:
        a = rest.pop(0)
        if a == "--comments" and rest:
            comments = rest.pop(0)
        elif a.startswith("--comments="):
            comments = a.split("=", 1)[1]
        elif report is None and not a.startswith("--"):
            report = a
        else:
            raise core.Fail(f"usage: {USAGE}")
    if report is None:
        raise core.Fail(f"usage: {USAGE}")
    return core.resolve(report), comments


def load_comments(value):
    """The `--comments` value as a list: a JSON array, or a file holding one."""
    if value is None:
        return None
    path = core.resolve(value)
    text = path.read_text(encoding="utf-8") if path.is_file() else value
    try:
        got = json.loads(text)
    except ValueError:
        raise core.Fail("bad-comments")
    if not isinstance(got, list) or not all(isinstance(c, dict) for c in got):
        raise core.Fail("bad-comments")
    return got


def step(ctx):
    if not re.fullmatch(r"PR\d+", ctx.id):
        raise core.Fail(f"bad-pr: {ctx.id}")
    number = ctx.id[2:]
    report, comments = parse(ctx.args)
    try:
        body = report.read_text(encoding="utf-8")
    except OSError:
        body = ""
    if not body.strip():
        ctx.note(f"report {report} is missing or empty")
        raise core.Fail("empty-review")
    comments = load_comments(comments)
    repo = core.resolve(runfolder.run_info(ctx.run_dir)["Repo"])
    res = gh.gh(["pr", "view", number, "--json", "headRefOid"], ctx.log, cwd=repo)
    try:
        head = json.loads(res.out).get("headRefOid") if res.rc == 0 else None
    except (ValueError, AttributeError):
        head = None
    if not head:
        raise core.Fail("pr-view")
    payload = {"commit_id": head, "event": "COMMENT", "body": body}
    if comments:
        payload["comments"] = comments
    payload_file = ctx.run_dir / "logs" / f"{ctx.id}-review.json"
    payload_file.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")
    ctx.note(f"report {report} ({len(body)} chars) on {head}")
    res = gh.gh(["api", f"repos/{{owner}}/{{repo}}/pulls/{number}/reviews", "--method", "POST",
                 "--input", payload_file], ctx.log, cwd=repo)
    try:
        url = json.loads(res.out).get("html_url") if res.rc == 0 else None
    except (ValueError, AttributeError):
        url = None
    if res.rc != 0:
        text = res.out + res.err
        if DENIED.search(text) and not RATE_LIMIT.search(text):
            ctx.note(f"GitHub refused the review; the findings stay in {report}")
            raise core.Fail(f"post-denied {report}")
        raise core.Fail("post")
    return "review", url or f"#{number}"


def pr_id(value):
    """`PR<n>` for a PR given as `n`, `#n`, `PR<n>` or a PR URL; the value itself when it names no number."""
    m = re.fullmatch(r"(?:#|PR)?(\d+)|https?://\S+/pull/(\d+)/?", value.strip())
    return f"PR{m.group(1) or m.group(2)}" if m else value


if __name__ == "__main__":
    argv = sys.argv[1:]
    if len(argv) >= 2:
        argv[1] = pr_id(argv[1])
    core.main("post_review", step, usage="<report-file> [--comments <json>]", argv=argv)
