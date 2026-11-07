"""`map.md`'s `Checks`: the suite commands a merge runs in the task's worktree, each timed.

The commands are the lines inside the fenced code blocks of the `## Checks` section, blank lines and `#` comments
skipped. Each runs under `bash -c` in the worktree, its output in the step's log. `save` keeps the results in
`<run>/reports/<id>.checks.json`: a list of `{command, rc, passed, failed, skipped, seconds, skip_reasons}`, the counts
read from the suite's output by `counts`, `null` when it says none.
"""
import json
import re
import time
from collections import namedtuple

from . import core

Suite = namedtuple("Suite", "command rc seconds out", defaults=("",))

# This repo's runners: `<suite>: ok`, `<suite>: skipped (why)` or `== <tool>: why, skipped (how)`, `== <suite>: FAILED`,
# read only inside their framing (`tests/parallel.sh`'s `== tests/a.test.sh (3s)`, `tests/run.sh`'s `== <step>`).
FRAMING = re.compile(r"^== [\w./*-]+(?: \(\d+s\)|: .*)?$")
REPO_LINE = re.compile(r"^(?:== )?([\w./-]+): (ok\b.*|.*\bskipped\b.*|FAILED\b.*)$")
# pytest: the `-rs` lines and the final `N passed, N skipped in Xs` line, with or without its `=` rule.
PYTEST_SKIP = re.compile(r"^SKIPPED \[\d+\] (?:\S+?:\d+: )?(.*)$")
PYTEST_SUMMARY = re.compile(r"^=*\s*((?:\d+ \w+(?:, )?)+) in [\d.]+s\b")
# unittest: `Ran N tests in Xs`, its `OK (skipped=N)` or `FAILED (failures=N, ...)` line, and verbose skip lines.
UNITTEST_RAN = re.compile(r"^Ran (\d+) tests? in ")
UNITTEST_END = re.compile(r"^(OK|FAILED)(?: \((.*)\))?\s*$")
UNITTEST_SKIP = re.compile(r"\.\.\. skipped '(.*)'$")
# Jest: `Tests:       1 failed, 2 skipped, 40 passed, 43 total`.
JEST_SUMMARY = re.compile(r"^Tests:\s+((?:\d+ \w+, )*)\d+ total\s*$")


def commands(map_md):
    """The suite commands of `map.md`'s `## Checks` section, in order. Fails `no-checks` when there are none."""
    path = core.resolve(map_md)
    if not path.is_file():
        raise core.Fail(f"no-map: {path}")
    m = re.search(r"^## Checks[ \t]*\n(.*?)(?=^## |\Z)", path.read_text(), re.MULTILINE | re.DOTALL)
    found = []
    for block in re.findall(r"^```[^\n]*\n(.*?)^```", m.group(1) if m else "", re.MULTILINE | re.DOTALL):
        found += [line.strip() for line in block.splitlines() if line.strip() and not line.strip().startswith("#")]
    if not found:
        raise core.Fail("no-checks: map.md has no command in a Checks code block")
    return found


def run_all(cmds, cwd, log):
    """Runs each command in `cwd`, timing it; stops at the first red one. Returns the `Suite` results so far."""
    done = []
    for cmd in cmds:
        start = time.monotonic()
        res = core.run(["bash", "-c", cmd], log, cwd=cwd)
        done.append(Suite(cmd, res.rc, time.monotonic() - start, res.out + res.err))
        log.write(f"suite {cmd!r}: rc {res.rc} in {done[-1].seconds:.1f}s\n")
        log.flush()
        if res.rc != 0:
            break
    return done


def _repo(lines):
    if not any(map(FRAMING.match, lines)):
        return None
    found = [m for m in map(REPO_LINE.match, lines) if m]
    if not found:
        return None
    passed = failed = 0
    reasons = []
    for m in found:
        name, rest = m.groups()
        if rest.startswith("ok"):
            passed += 1
        elif rest.startswith("FAILED"):
            failed += 1
        else:
            why = re.sub(r",?\s*\bskipped\b[,:]?\s*", " ", rest).strip()
            if why.startswith("(") and why.endswith(")"):
                why = why[1:-1]
            reasons.append(f"{name}: {why}" if why else name)
    return passed, failed, len(reasons), reasons


def _pytest(lines):
    totals, seen = {}, False
    for m in filter(None, map(PYTEST_SUMMARY.match, lines)):
        seen = True
        for n, word in re.findall(r"(\d+) (\w+)", m.group(1)):
            totals[word] = totals.get(word, 0) + int(n)
    if not seen:
        return None
    reasons = [m.group(1) for m in map(PYTEST_SKIP.match, lines) if m]
    failed = totals.get("failed", 0) + totals.get("error", 0) + totals.get("errors", 0)
    return totals.get("passed", 0), failed, totals.get("skipped", 0), reasons


def _unittest(lines):
    ran = passed = failed = skipped = 0
    for i, line in enumerate(lines):
        m = UNITTEST_RAN.match(line)
        end = next((e for e in map(UNITTEST_END.match, lines[i + 1:i + 4]) if e), None) if m else None
        if not end:
            continue
        ran += 1
        n = {k.strip(): int(v) for k, v in re.findall(r"([\w ]+)=(\d+)", end.group(2) or "")}
        bad = n.get("failures", 0) + n.get("errors", 0) + n.get("unexpected successes", 0)
        failed += bad
        skipped += n.get("skipped", 0)
        passed += int(m.group(1)) - bad - n.get("skipped", 0)
    if not ran:
        return None
    return passed, failed, skipped, [m.group(1) for m in map(UNITTEST_SKIP.search, lines) if m]


def _jest(lines):
    totals, seen = {}, False
    for m in filter(None, map(JEST_SUMMARY.match, lines)):
        seen = True
        for n, word in re.findall(r"(\d+) (\w+)", m.group(1)):
            totals[word] = totals.get(word, 0) + int(n)
    if not seen:
        return None
    return totals.get("passed", 0), totals.get("failed", 0), totals.get("skipped", 0) + totals.get("todo", 0), []


def counts(out):
    """`(passed, failed, skipped, skip_reasons)` read from a suite's output, or `(None, None, None, [])`.

    Inside this repo's runner framing its suite lines win, since `tests/run.sh` also prints the unittest output of the
    suites it runs; elsewhere a `<word>: ok` line is any program's log, and pytest's summary, then unittest's, then
    Jest's count. Several summaries of one kind (one per folder) are summed.
    """
    lines = out.splitlines()
    for parse in (_repo, _pytest, _unittest, _jest):
        found = parse(lines)
        if found:
            return found
    return None, None, None, []


def save(run, ident, suites):
    """Writes `suites` to `<run>/reports/<ident>.checks.json`, replacing an earlier merge's file."""
    path = core.resolve(run) / "reports" / f"{ident}.checks.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for s in suites:
        passed, failed, skipped, reasons = counts(s.out)
        rows.append({"command": s.command, "rc": s.rc, "passed": passed, "failed": failed, "skipped": skipped,
                     "seconds": round(s.seconds, 1), "skip_reasons": reasons})
    path.write_text(json.dumps(rows, indent=1) + "\n")
