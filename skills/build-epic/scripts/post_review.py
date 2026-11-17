#!/usr/bin/env python3
"""Posts a reviewer's output, rendered for a human, as one COMMENT review on a PR's head commit. It never approves.

Usage: python3 -I post_review.py <run> <PR> <report-file> [--base <ref>] [--note <text>]

`<PR>` is a number, `#n` or a PR URL. The report file is the reviewer's output object (the agent protocol,
`_shared/protocol`): a `reviewer` one, or a `security` one when its name ends `-security.json`. The report path is
resolved against the caller's folder before anything else runs, so a relative path posts the same body as an absolute
one. The body is one header line, a blank line, the output rendered by `_shared/protocol/render.py`, then the note
when given, as its own paragraph: never the agent's own text. The header names only what is known about the review:
the PR and head it reviewed, read from the report's name (`<PR>-<sha7>[-<suffix>].json`, or `<TT-NN>-<sha7>...` for a
task reviewed before it merged, which has no PR; with any other name, the reviewed head is not recorded), `posted on
PR #<n>` when that PR is not the one posted on (a task's report on its ticket PR), the diff read (`git diff
<ref>...<sha7>`) only when `--base` gives the base the reviewer was given (for a re-review, the head it reviewed
before), and the run's `test_command:` from `decisions.md`. There are no inline comments: findings cite `path:line` in
the rendered text. The body is saved beside the report as `<name>.posted.md`, and the payload `{commit_id, event:
COMMENT, body}` is kept in `<run>/logs/PR<n>-review.json` and posted with `gh api repos/{owner}/{repo}/pulls/<n>/reviews`.

Prints `OK PR<n> review <url>`, or `FAIL PR<n> <reason> (log: ...)`: `empty-review` (the file is missing or blank),
`bad-report` (not UTF-8), `brief-violation <problems>` (not one JSON object matching its role's output schema, the
problems as `check.py` names them; nothing is posted), `pr-view`, `post`, or `post-denied <report-file>` when GitHub
refuses the review (HTTP 403 other than a rate limit, never retried; a rate-limit 403 is a plain `post` failure, so it
can be retried): the report stays where it is, so the run's report can name it.
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_shared" / "protocol"))

import protocol  # noqa: E402
import render  # noqa: E402
from steplib import core, decisions, gh, runfolder  # noqa: E402

DENIED = re.compile(r"HTTP 403|returned error: 403")
RATE_LIMIT = re.compile(r"rate limit", re.IGNORECASE)
USAGE = "post_review.py <run> <PR> <report-file> [--base <ref>] [--note <text>]"
REPORT_NAME = re.compile(r"(\d+)-([0-9a-f]{7,40})(?:-.*)?")
TASK_REPORT = re.compile(r"(\d+-\d+)-([0-9a-f]{7,40})(?:-.*)?")  # a task's review before it merged: no PR of its own


def parse(args):
    """(report path, note, base) from the arguments after the PR, the report path already resolved."""
    report, opts, rest = None, {"note": None, "base": None}, list(args)
    while rest:
        a = rest.pop(0)
        key = a[2:].split("=", 1)[0] if a.startswith("--") else None
        if key in opts and "=" in a:
            opts[key] = a.split("=", 1)[1]
        elif key in opts and rest:
            opts[key] = rest.pop(0)
        elif report is None and not a.startswith("--"):
            report = a
        else:
            raise core.Fail(f"usage: {USAGE}")
    if report is None:
        raise core.Fail(f"usage: {USAGE}")
    return core.resolve(report), opts["note"], opts["base"]


def header(number, report, base, test_command):
    """The one line above the report: what the reviewer read, as far as its name and `base` say, and what it ran."""
    task = TASK_REPORT.fullmatch(report.stem)
    m = task or REPORT_NAME.fullmatch(report.stem)
    reviewed, sha = (m.group(1), m.group(2)[:7]) if m else (number, None)
    if task:
        line = f"Review of task {reviewed} at `{sha}`, before it merged"
    else:
        line = f"Review of PR #{reviewed} at `{sha}`" if sha else f"Review of PR #{reviewed} (reviewed head not recorded)"
    if task or reviewed != number:
        line += f", posted on PR #{number}"
    if base and sha:
        line += f": read `git diff {base}...{sha}` (from the merge base)"
    ran = f"`{test_command}`" if test_command else "no test command recorded"
    return f"{line}; the run's tests: {ran}. The review below is rendered from the reviewer's report."


def role(report):
    """The protocol role of the report: `security` for a security reviewer's (`...-security.json`), else `reviewer`."""
    return "security" if report.stem.endswith("-security") else "reviewer"


def rendered(report, text):
    """The report's output rendered for a human; `brief-violation` when it is not a valid output of its role."""
    try:
        out = protocol.parse_json(text)
    except ValueError:
        problems = ["json"]
    else:
        problems = protocol.check(role(report), "output", out)
    if problems:
        raise core.Fail(f"brief-violation {'; '.join(problems)}")
    return render.render(role(report), out)


def with_note(body, note):
    """`body` with `note` after it as its own paragraph."""
    if not note:
        return body
    return body + ("\n" if body.endswith("\n") else "\n\n") + note + "\n"


def step(ctx):
    if not re.fullmatch(r"PR\d+", ctx.id):
        raise core.Fail(f"bad-pr: {ctx.id}")
    number = ctx.id[2:]
    report, note, base = parse(ctx.args)
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
    try:
        review = rendered(report, text)
    except core.Fail:
        ctx.note(f"report {report} is not a valid {role(report)} output; nothing posted")
        raise
    repo = core.resolve(runfolder.run_info(ctx.run_dir)["Repo"])
    res = gh.gh(["pr", "view", number, "--json", "headRefOid"], ctx.log, cwd=repo)
    try:
        head = (json.loads(res.out) if res.rc == 0 else {}).get("headRefOid")
    except (ValueError, AttributeError):
        head = None
    if not head:
        raise core.Fail("pr-view")
    body = with_note(header(number, report, base, decisions.get(ctx.run_dir, "test_command")) + "\n\n" + review, note)
    with open(report.with_name(f"{report.stem}.posted.md"), "w", encoding="utf-8", newline="") as f:
        f.write(body)
    payload = {"commit_id": head, "event": "COMMENT", "body": body}
    payload_file = ctx.run_dir / "logs" / f"{ctx.id}-review.json"
    payload_file.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")
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
    core.main("post_review", step, usage="<report-file> [--base <ref>] [--note <text>]", argv=argv)
