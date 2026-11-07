#!/usr/bin/env python3
"""Decides a test-only tuning PR with no agent: build-epic step 6's Merge of a `TT-tests` task (behaviour 49).

Usage: python3 -I verify_tuner.py <run> <TT-tests> [--report <file>] [--timeout <secs>]

In the tuning task's worktree `<run>/worktrees/<TT-tests>`, against its fork point from the ticket branch (committed,
uncommitted and untracked changes alike):
- only test files changed: a file named like `test_*`, `*_test.*`, `*.test.*`, `*.spec.*` or `conftest.py`, any file
  under a `fixtures`, `testdata` (...) folder, or a code file under a `test`, `tests`, `spec` (...) folder. A test
  harness script or config there (`tests/run.sh`, `tests/suites.txt`) is not a test file;
- no assertion line (`assert...`, `expect(`, `.Fatal(`, a shell `check ...`, `... || fail`, `[ ... ] || exit 1`, ...)
  is removed unless the diff of tracked files adds the same line back or a `moved:` entry of the report names it, by
  its text or by its old `file:line`. An untracked file's lines never count as the line added back;
- the report has at least one `mutations:` entry per `covered:` entry, and no mutation targets a test file;
- `map.md`'s `Checks` are green;
- each `mutations:` entry of the report (default `<run>/reports/<TT-tests>.md`), `file:line old -> new`, turns them
  red: the first `old` on that line becomes `new`, the Checks run, and the file gets its bytes and times back on any
  exit, SIGTERM and SIGINT included. A suite killed by its time limit counts as red.

Every suite run has a time limit: `--timeout`, else the `--secs N` of a `capped` Checks line, else 600 s for the green
run and three times its green time (at least 30 s) for a mutated one. The suite runs in its own process group, killed
whole at the limit, with `PYTHONDONTWRITEBYTECODE=1` so no stale `.pyc` hides a mutation and none is left behind.

Report entries are `key: value` lines; indented or `- ` lines under a key add values to it; `none` is no value.

Prints `OK <TT-tests> verified mutations=<n>`, or `FAIL <TT-tests> <reason> (log: ...)`: `no-report`,
`bad-mutation <file:line>`, `test-file-mutation <file:line>`, `unproven-covered covered=<n> mutations=<m>`,
`non-test-file <path>`, `assertion-removed <file:line>`, `red <suite>`,
`timeout <suite>`, `mutation-survived <file:line>`, `interrupted`. FAIL sends the PR to an agent review.
"""
import os
import re
import signal
import subprocess
import sys
import time
from collections import namedtuple
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parent))

from steplib import checks, core, runfolder  # noqa: E402

GREEN_SECS = 600
# Under these folders every file is test data.
FIXTURE_DIRS = {"testdata", "fixtures", "__fixtures__", "__snapshots__"}
# Under these folders a code file is a test (a helper included); a shell script, config or data file is harness.
TEST_DIRS = {"test", "tests", "__tests__", "spec", "specs"}
TEST_CODE_EXT = {".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".go", ".rb", ".java", ".kt", ".rs", ".c",
                 ".cc", ".cpp", ".cs", ".php", ".swift", ".scala", ".ex", ".exs"}
TEST_NAME = re.compile(r"^(test_.*|conftest\.py|.*[._-](test|tests|spec)\.[^.]+|.*\.(test|spec)\.[^.]+\.snap"
                       r"|.*Tests?\.[^.]+)$")
ASSERTION = re.compile(r"\bassert|\bexpect\s*\(|\.(Fatal|Fatalf|Error|Errorf)\(|\bshould\b"
                       # Shell: a check helper, or a command whose failure fails the test.
                       r"|^\s*(check|expect|fail_unless|refute)\w*\s"
                       r"|(\|\||&&)\s*(fail|die|bail|false|exit\s+[1-9]|return\s+[1-9])\b")
ENTRY = re.compile(r"^(?P<file>[^\s:]+):(?P<line>\d+)\s+(?P<old>.+?)\s+->(?:\s+(?P<new>.*))?$")

Mutation = namedtuple("Mutation", "file line old new")


def unquote(text):
    text = text.strip()
    return text[1:-1] if len(text) > 1 and text[0] == text[-1] == "`" else text


def report_entries(path):
    """The report's values by key: `{"mutations": [...], "moved": [...], ...}`."""
    entries, key = {}, None
    for line in path.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("```"):
            continue
        m = re.match(r"^([A-Za-z][\w-]*):\s*(.*)$", line)
        if m:
            key, value = m.group(1).lower(), m.group(2)
        elif key and (line[0].isspace() or line.startswith(("- ", "* "))):
            value = line.strip()[2:] if line.strip()[:2] in ("- ", "* ") else line.strip()
        else:
            key = None
            continue
        value = unquote(value)
        if value and value.lower() not in ("none", "n/a", "-"):
            entries.setdefault(key, []).append(value)
    return entries


def mutation(entry):
    m = ENTRY.match(entry)
    if not m:
        raise core.Fail(f"bad-mutation {entry.split()[0] if entry.split() else '?'}")
    return Mutation(m.group("file"), int(m.group("line")), unquote(m.group("old")), unquote(m.group("new") or ""))


def is_test_file(path):
    parts = PurePosixPath(path).parts
    dirs, name = set(parts[:-1]), parts[-1]
    if FIXTURE_DIRS & dirs or TEST_NAME.match(name):
        return True
    return bool(TEST_DIRS & dirs) and PurePosixPath(name).suffix.lower() in TEST_CODE_EXT


def git(ctx, wt, *args):
    res = ctx.run(["git", "-C", wt, *args])
    if res.rc != 0:
        raise core.Fail(f"git-{args[0]}")
    return res.out


def fork_point(ctx, wt, run, tt):
    """Where the tuning branch left the ticket branch: its merge base with `origin/<branch>` (or `<branch>`)."""
    branch = runfolder.get(runfolder.ticket_file(run, tt), "Branch")
    if not branch:
        raise core.Fail("ticket-no-branch")
    for ref in (f"origin/{branch}", branch):
        res = ctx.run(["git", "-C", wt, "merge-base", "HEAD", ref])
        if res.rc == 0 and res.out.strip():
            return res.out.strip()
    raise core.Fail(f"no-base {branch}")


def changes(ctx, wt, base):
    """(changed paths, removed lines as (file, old line, text), added line texts) of the worktree against `base`.
    Untracked files are among the paths, but their lines are not added ones: they are in no commit."""
    paths = git(ctx, wt, "diff", "--name-only", "--no-renames", base).split("\n")
    untracked = git(ctx, wt, "ls-files", "--others", "--exclude-standard").split("\n")
    removed, added = [], set()
    old_file, old_line = None, 0
    for line in git(ctx, wt, "diff", "-U0", "--no-color", "--no-renames", "--no-ext-diff", base).splitlines():
        if line.startswith("--- "):
            old_file = line[6:] if line.startswith("--- a/") else None
        elif line.startswith("+++ "):
            continue
        elif line.startswith("@@"):
            old_line = int(re.match(r"@@ -(\d+)", line).group(1))
        elif line.startswith("-"):
            removed.append((old_file, old_line, line[1:].strip()))
            old_line += 1
        elif line.startswith("+"):
            added.add(line[1:].strip())
    return [p for p in paths + untracked if p], removed, added


def check_assertions(removed, added, moved):
    said = [" ".join(unquote(m).split()) for m in moved]
    for file, line, text in removed:
        if not ASSERTION.search(text) or text.startswith(("#", "//")) or text in added:
            continue
        flat = " ".join(text.split())
        if not any(flat in m or f"{file}:{line}" in m for m in said):
            raise core.Fail(f"assertion-removed {file}:{line}")


def secs_of(command):
    m = re.search(r"(?:^|\s)--secs[ =](\d+)", command)
    return int(m.group(1)) if m else None


def run_suite(ctx, wt, command, limit):
    """Runs one Checks command in its own process group, killed whole after `limit` seconds. Returns rc, or None
    when it hit the limit. The group is killed on any exit, so an interrupt leaves no mutated test running."""
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    ctx.note(f"$ {command}  (in {wt}, limit {limit}s)")
    posix = os.name == "posix"
    # `core.command` finds `bash` on `PATH`: a bare `bash` on Windows is WSL's, which runs no Git Bash command line.
    p = subprocess.Popen(core.command(["bash", "-c", command], env), cwd=str(wt), env=env, stdin=subprocess.DEVNULL,
                         stdout=ctx.log, stderr=subprocess.STDOUT, start_new_session=posix,
                         creationflags=0 if posix else subprocess.CREATE_NEW_PROCESS_GROUP)
    try:
        rc = p.wait(timeout=limit)
    except subprocess.TimeoutExpired:
        rc = None
    finally:
        if posix:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except OSError:
                pass
        elif p.poll() is None:
            # Killing `bash` alone leaves the suite it started running: take the whole tree.
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if p.poll() is None:
                p.kill()
        p.wait()
    ctx.note(f"timeout after {limit}s" if rc is None else f"[rc {rc}]")
    return rc


def run_checks(ctx, wt, commands, limits, times=None):
    """Runs each command until the first red one, noting each one's seconds in `times`. Returns (command, rc or None)
    of the last one run."""
    for command in commands:
        start = time.monotonic()
        rc = run_suite(ctx, wt, command, limits(command))
        if times is not None:
            times[command] = time.monotonic() - start
        if rc != 0:
            break
    return command, rc


def mutated(path, m):
    """The file's bytes with the first `m.old` on line `m.line` replaced by `m.new`; None when `m.old` is not there."""
    data = path.read_bytes()
    lines = data.splitlines(keepends=True)
    if not 1 <= m.line <= len(lines):
        return None
    text = lines[m.line - 1].decode("utf-8", errors="surrogateescape")
    if m.old not in text:
        return None
    lines[m.line - 1] = text.replace(m.old, m.new, 1).encode("utf-8", errors="surrogateescape")
    return b"".join(lines)


def raise_interrupt(signum, frame):
    raise KeyboardInterrupt(f"signal {signum}")


def apply_and_run(ctx, wt, m, run):
    """Writes mutation `m`, calls `run()`, and gives the file its bytes and times back whatever happens."""
    path = wt / m.file
    original, st = path.read_bytes(), path.stat()
    new = mutated(path, m)
    try:
        path.write_bytes(new)
        # A newer mtime, so no cached compile of the old bytes passes for the mutated file.
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))
        return run()
    finally:
        held = {s: signal.signal(s, signal.SIG_IGN) for s in catchable()}
        try:
            path.write_bytes(original)
            os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))
        finally:
            for s, h in held.items():
                signal.signal(s, h)


def catchable():
    return [getattr(signal, n) for n in ("SIGINT", "SIGTERM", "SIGHUP") if hasattr(signal, n)]


def step(ctx):
    for s in catchable():
        signal.signal(s, raise_interrupt)
    run = ctx.run_dir
    args = list(ctx.args)
    report, timeout = run / "reports" / f"{ctx.id}.md", None
    while args:
        flag = args.pop(0)
        if flag in ("--report", "--timeout") and args:
            value = args.pop(0)
            if flag == "--report":
                report = core.resolve(value)
            else:
                timeout = int(value)
        else:
            raise core.Fail(f"usage: unknown argument {flag}")
    wt = run / "worktrees" / ctx.id
    if not wt.is_dir():
        raise core.Fail(f"no-worktree: {wt}")
    wt = core.resolve(wt)
    if not report.is_file():
        raise core.Fail(f"no-report {report}")
    entries = report_entries(report)
    mutations = [mutation(e) for e in entries.get("mutations", [])]
    for m in mutations:
        target = core.resolve(wt / m.file)
        if (os.path.isabs(m.file) or (target != wt and wt not in target.parents) or not target.is_file()
                or mutated(target, m) is None):
            raise core.Fail(f"bad-mutation {m.file}:{m.line}")
        if is_test_file(target.relative_to(wt).as_posix()):
            raise core.Fail(f"test-file-mutation {m.file}:{m.line}")
    covered = len(entries.get("covered", []))
    if covered > len(mutations):
        raise core.Fail(f"unproven-covered covered={covered} mutations={len(mutations)}")

    base = fork_point(ctx, wt, run, ctx.id.split("-", 1)[0])
    ctx.note(f"base {base}")
    paths, removed, added = changes(ctx, wt, base)
    for p in paths:
        if not is_test_file(p):
            raise core.Fail(f"non-test-file {p}")
    check_assertions(removed, added, entries.get("moved", []))

    commands = checks.commands(run / "map.md")
    green = {}

    def green_limit(command):
        return timeout or secs_of(command) or GREEN_SECS

    command, rc = run_checks(ctx, wt, commands, green_limit, green)
    if rc is None:
        raise core.Fail(f"timeout {command}")
    if rc != 0:
        raise core.Fail(f"red {command}")
    ctx.note("green")

    def mutant_limit(command):
        return timeout or secs_of(command) or max(30, int(3 * green.get(command, 10)))

    for m in mutations:
        ctx.note(f"mutation {m.file}:{m.line} {m.old!r} -> {m.new!r}")
        _, rc = apply_and_run(ctx, wt, m, lambda: run_checks(ctx, wt, commands, mutant_limit))
        if rc == 0:
            raise core.Fail(f"mutation-survived {m.file}:{m.line}")
        ctx.note(f"killed {m.file}:{m.line}")
    return "verified", f"mutations={len(mutations)}"


if __name__ == "__main__":
    core.main("verify_tuner", step)
