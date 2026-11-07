#!/usr/bin/env python3
"""Posts a reviewer's own report as one COMMENT review on a PR's head commit. It never approves.

Usage: python3 -I post_review.py <run> <PR> <report-file> [--note <text>]

`<PR>` is a number, `#n` or a PR URL. The report path is resolved against the caller's folder before anything else
runs, so a relative path posts the same body as an absolute one. The body is one header line (the PR, the head commit
and the merge-base diff read, and the run's `test_command:` from `decisions.md`), a blank line, the report file's
bytes unchanged, then the note when given, as its own paragraph. There are no inline comments: findings cite
`path:line` in the report's text. The body is saved beside the report as `<name>.posted<ext>`, and the payload
`{commit_id, event: COMMENT, body}` is kept in `<run>/logs/PR<n>-review.json` and posted with
`gh api repos/{owner}/{repo}/pulls/<n>/reviews`.

Prints `OK PR<n> review <url>`, or `FAIL PR<n> <reason> (log: ...)`: `empty-review` (the file is missing or blank),
`bad-report` (not UTF-8), `pr-view`, `post`, or `post-denied <report-file>` when GitHub refuses the review (HTTP 403
other than a rate limit, never retried; a rate-limit 403 is a plain `post` failure, so it can be retried): the report
stays where it is, so the run's report can name it.
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from steplib import core, decisions, gh, runfolder  # noqa: E402

DENIED = re.compile(r"HTTP 403|returned error: 403")
RATE_LIMIT = re.compile(r"rate limit", re.IGNORECASE)
USAGE = "post_review.py <run> <PR> <report-file> [--note <text>]"


def parse(args):
    """(report path, note) from the arguments after the PR, the report path already resolved."""
    report, note, rest = None, None, list(args)
    while rest:
        a = rest.pop(0)
        if a == "--note" and rest:
            note = rest.pop(0)
        elif a.startswith("--note="):
            note = a.split("=", 1)[1]
        elif report is None and not a.startswith("--"):
            report = a
        else:
            raise core.Fail(f"usage: {USAGE}")
    if report is None:
        raise core.Fail(f"usage: {USAGE}")
    return core.resolve(report), note


def header(number, head, base, test_command):
    """The one line above the report: what the reviewer read and what it ran."""
    ran = f"`{test_command}`" if test_command else "no test command recorded"
    return (f"Review of PR #{number} at `{head[:7]}`: read `git diff {base}...{head[:7]}` (from the merge base); "
            f"the run's tests: {ran}. The report below is the reviewer's own.")


def with_note(body, note):
    """`body` with `note` after it as its own paragraph."""
    if not note:
        return body
    return body + ("\n" if body.endswith("\n") else "\n\n") + note + "\n"


def step(ctx):
    if not re.fullmatch(r"PR\d+", ctx.id):
        raise core.Fail(f"bad-pr: {ctx.id}")
    number = ctx.id[2:]
    report, note = parse(ctx.args)
    try:
        data = report.read_bytes()
    except OSError:
        data = b""
    if not data.strip():
        ctx.note(f"report {report} is missing or empty")
        raise core.Fail("empty-review")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        ctx.note(f"report {report} is not UTF-8")
        raise core.Fail("bad-report")
    repo = core.resolve(runfolder.run_info(ctx.run_dir)["Repo"])
    res = gh.gh(["pr", "view", number, "--json", "headRefOid,baseRefName"], ctx.log, cwd=repo)
    try:
        view = json.loads(res.out) if res.rc == 0 else {}
        head, base = view.get("headRefOid"), view.get("baseRefName") or "base"
    except (ValueError, AttributeError):
        head = None
    if not head:
        raise core.Fail("pr-view")
    body = with_note(header(number, head, base, decisions.get(ctx.run_dir, "test_command")) + "\n\n" + text, note)
    with open(report.with_name(f"{report.stem}.posted{report.suffix}"), "w", encoding="utf-8", newline="") as f:
        f.write(body)
    payload = {"commit_id": head, "event": "COMMENT", "body": body}
    payload_file = ctx.run_dir / "logs" / f"{ctx.id}-review.json"
    payload_file.write_text(json.dumps(payload, indent=1) + "\n")
    ctx.note(f"report {report} ({len(data)} bytes) on {head}")
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
    core.main("post_review", step, usage="<report-file> [--note <text>]", argv=argv)
