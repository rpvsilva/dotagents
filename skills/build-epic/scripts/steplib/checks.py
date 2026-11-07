"""`map.md`'s `Checks`: the suite commands a merge runs in the task's worktree, each timed.

The commands are the lines inside the fenced code blocks of the `## Checks` section, blank lines and `#` comments
skipped, except the block right after a `Setup:` line (the one-time Setup `setup.py` runs). Each runs under `bash -c`
in the worktree, its output in the step's log. `save` keeps the results in `<run>/reports/<id>.checks.json`, the shape
`prbody.py` reads. `skip_reasons` reads the skipped tests out of a suite's output, each with the tool it names.
"""
import json
import re
import time
from collections import namedtuple

from . import core

Suite = namedtuple("Suite", "command rc seconds")
Skip = namedtuple("Skip", "tool reason")

PYTEST_SKIP = re.compile(r"^SKIPPED \[\d+\] [^:]+:\d+: (.+)$")
UNITTEST_SKIP = re.compile(r" \.\.\. skipped (['\"])(.*)\1$")
SKIPPED = re.compile(r"\bskipped\b", re.IGNORECASE)
UNITTEST_SUMMARY = re.compile(r"^(?:OK|FAILED) \(.*\)$")
TOOL_BEFORE = re.compile(r"([\w.+-]+)\W* (?:is |are )?not (?:installed|found|available|on PATH)", re.IGNORECASE)
TOOL_AFTER = re.compile(r"\b(?:requires|needs|without|missing|install|no) ([\w.+-]+)", re.IGNORECASE)


def commands(map_md):
    """The suite commands of `map.md`'s `## Checks` section, in order. Fails `no-checks` when there are none."""
    path = core.resolve(map_md)
    if not path.is_file():
        raise core.Fail(f"no-map: {path}")
    m = re.search(r"^## Checks[ \t]*\n(.*?)(?=^## |\Z)", path.read_text(), re.MULTILINE | re.DOTALL)
    found, section = [], m.group(1) if m else ""
    for block in re.finditer(r"^```[^\n]*\n(.*?)^```", section, re.MULTILINE | re.DOTALL):
        before = section[:block.start()].rstrip().rsplit("\n", 1)[-1]
        if before.startswith("Setup:"):  # the one-time Setup's block (`setup.py`), not a suite
            continue
        found += [line.strip() for line in block.group(1).splitlines()
                  if line.strip() and not line.strip().startswith("#")]
    if not found:
        raise core.Fail("no-checks: map.md has no command in a Checks code block")
    return found


def run_all(cmds, cwd, log):
    """Runs each command in `cwd`, timing it; stops at the first red one. Returns the `Suite` results so far."""
    done = []
    for cmd in cmds:
        start = time.monotonic()
        res = core.run(["bash", "-c", cmd], log, cwd=cwd)
        done.append(Suite(cmd, res.rc, time.monotonic() - start))
        log.write(f"suite {cmd!r}: rc {res.rc} in {done[-1].seconds:.1f}s\n")
        log.flush()
        if res.rc != 0:
            break
    return done


def save(run, ident, suites):
    """Writes `suites` to `<run>/reports/<ident>.checks.json` as a list of `{command, rc, passed, seconds}`."""
    path = core.resolve(run) / "reports" / f"{ident}.checks.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [{"command": s.command, "rc": s.rc, "passed": s.rc == 0, "seconds": round(s.seconds, 1)} for s in suites]
    path.write_text(json.dumps(rows, indent=1) + "\n")


def tool(reason):
    """The tool a skip reason names as missing (`zsh is not installed`, `requires docker`), or None."""
    m = TOOL_BEFORE.search(reason) or TOOL_AFTER.search(reason)
    return m.group(1) if m else None


def skip_reasons(text):
    """Every skipped test or suite in a suite's output, in order, as `Skip(tool, reason)`: pytest's `-rs` summary
    (`SKIPPED [n] file:line: reason`), unittest's verbose `... skipped 'reason'`, and any other line saying
    `skipped`, as this repo's suites print (`<suite>: skipped (reason)`). unittest's `OK (skipped=N)` summary names no
    reason and is not one."""
    found = []
    for line in text.splitlines():
        line = line.strip()
        m = PYTEST_SKIP.match(line) or UNITTEST_SKIP.search(line)
        if m:
            reason = m.group(m.lastindex)
        elif SKIPPED.search(line) and not UNITTEST_SUMMARY.match(line):
            reason = line
        else:
            continue
        found.append(Skip(tool(reason), reason))
    return found
