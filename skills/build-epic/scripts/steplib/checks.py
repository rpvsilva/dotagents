"""`map.md`'s `Checks`: the suite commands a merge runs in the task's worktree, each timed.

The commands are the lines inside the fenced code blocks of the `## Checks` section, blank lines and `#` comments
skipped, except the block right after a `Setup:` or `Setup (<anything>):` line (the one-time Setup `setup.py` runs).
Each runs under `bash -c` in the worktree, inside the run's environment (`in_env`), its output in the step's log.
`save` keeps the results in `<run>/reports/<id>.checks.json`: a list of `{command, rc, passed, failed, skipped,
seconds, skip_reasons}`, the counts and reasons read from the suite's output by `counts`, `null` when it says none.
`skip_reasons` is those reasons, each once, with the tool it names (what `setup.py` lists in `skips.md`).
"""
import json
import re
import shlex
import time
from collections import namedtuple

from . import core, decisions

ENV_VAR = "BUILD_EPIC_ENV"
SETUP_DONE = ".setup-done"
# A line about the one-time Setup, and the forms `setup.py` reads: `Setup: ...` and `Setup (<anything>): ...`.
SETUP_LINE = re.compile(r"^Setup\b.*$", re.MULTILINE)
SETUP_HEADING = re.compile(r"^Setup(?:[ \t]*\([^()\n]*\))?:[ \t]*(.*)$")

Suite = namedtuple("Suite", "command rc seconds out", defaults=("",))
Skip = namedtuple("Skip", "tool reason")

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
SKIPPED = re.compile(r"\bskipped\b", re.IGNORECASE)
# A tool name: a letter, then at least one more name character, not ending in punctuation. An optional version may
# follow it before the verb (`node 22.6+ is missing`, `shellcheck: 0.11.0 not installed`).
TOOL_NAME = r"[A-Za-z][\w.+-]*[\w+]"
TOOL_BEFORE = re.compile(rf"\b({TOOL_NAME})(?:\W+v?\d[\w.+-]*)?\W*\s(?:is |are )?"
                         r"(?:not (?:installed|found|available|on PATH)|missing)\b", re.IGNORECASE)
TOOL_AFTER = re.compile(rf"\b(?:requires|needs|without|missing|install|no) ({TOOL_NAME})", re.IGNORECASE)
# Words the patterns above catch that name no tool (`no such file`, `install a newer one`, `tests not found`).
NOT_TOOLS = {"a", "an", "the", "such", "any", "some", "this", "that", "these", "those", "it", "its", "them", "one",
             "more", "longer", "other", "network", "access", "permission", "support", "test", "tests", "file", "files",
             "module", "modules", "package", "packages", "dependency", "dependencies", "is", "are", "be", "and", "or",
             "to", "for", "with", "in", "on", "of", "not", "run", "running", "need", "needed", "needs", "yet"}


def env_prefix(run):
    """The shell commands that put a suite in the run's environment, as `setup.py` made it: `export BUILD_EPIC_ENV=
    <run>/env` once its Setup succeeded (`env/.setup-done`), then `. <run>/env/bin/activate` (`Scripts/activate` on Windows) when that exists. Paths
    are written with `/`, which bash reads on every platform (Git Bash takes `C:/x`)."""
    env = core.resolve(run) / "env"
    if not (env / SETUP_DONE).is_file():
        return []
    parts = [f"export {ENV_VAR}={shlex.quote(env.as_posix())}"]
    for folder in ("bin", "Scripts"):  # a Windows virtualenv keeps it in `Scripts/`
        if (env / folder / "activate").is_file():
            parts.append(f". {shlex.quote((env / folder / 'activate').as_posix())}")
            break
    return parts


def caps_prefix(run, caps=None):
    """The export giving `capped` with no flags the run's caps: `caps` (`"<mb> <secs>"`), else `decisions.md`'s
    `caps:` (`setup.py`), as defaults only, so a sandbox's own CAPPED_MB and CAPPED_SECS (its share of its machine)
    win; nothing when neither is set."""
    caps = caps or decisions.get(run, "caps")
    if not caps:
        return []
    mb, secs = caps.split()
    return [f'export CAPPED_MB="${{CAPPED_MB:-{int(mb)}}}" CAPPED_SECS="${{CAPPED_SECS:-{int(secs)}}}"']


# Puts `capped` (`~/.agents/bin/capped`) within reach whatever PATH the shell has, appended so it shadows nothing.
# `$HOME` expands where the line runs, so a remote sandbox finds its own copy.
CAPPED_PATH = 'export PATH="$PATH:$HOME/.agents/bin"'


def in_env(run, command, caps=None):
    """`command` run with `capped` on PATH (`CAPPED_PATH`), under the run's caps (`caps_prefix`) and inside its
    environment (`env_prefix`), as one `bash -c` line."""
    return " && ".join([CAPPED_PATH] + caps_prefix(run, caps) + env_prefix(run) + [command])


REMOTE_ENV = "$HOME/.build-epic-env"


def remote_in_env(setup, command, caps):
    """`command` as a remote agent runs it: like `in_env`, but the environment is the sandbox's own
    (`$HOME/.build-epic-env`), made there by `setup` (map.md's Setup commands) the first time the line runs, since the
    run's `env/` is on this machine. `caps` (`"<mb> <secs>"`) are defaults only: the sandbox's own exports win."""
    parts = [CAPPED_PATH]
    if caps:
        mb, secs = caps.split()
        parts.append(f'export CAPPED_MB="${{CAPPED_MB:-{int(mb)}}}" CAPPED_SECS="${{CAPPED_SECS:-{int(secs)}}}"')
    if setup:
        done = f'"${ENV_VAR}/{SETUP_DONE}"'
        made = " && ".join([f"( {cmd} )" for cmd in setup] + [f"touch {done}"])
        parts += [f'export {ENV_VAR}="{REMOTE_ENV}"', f"{{ [ -f {done} ] || {{ {made}; }}; }}",
                  f'{{ [ ! -f "${ENV_VAR}/bin/activate" ] || . "${ENV_VAR}/bin/activate"; }}']
    return " && ".join(parts + [command])


def commands(map_md):
    """The suite commands of `map.md`'s `## Checks` section, in order. Fails `no-checks` when there are none."""
    path = core.resolve(map_md)
    if not path.is_file():
        raise core.Fail(f"no-map: {path}")
    m = re.search(r"^## Checks[ \t]*\n(.*?)(?=^## |\Z)", path.read_text(encoding="utf-8"), re.MULTILINE | re.DOTALL)
    found, section = [], m.group(1) if m else ""
    for block in re.finditer(r"^```[^\n]*\n(.*?)^```", section, re.MULTILINE | re.DOTALL):
        before = section[:block.start()].rstrip().rsplit("\n", 1)[-1]
        if SETUP_LINE.match(before):  # the one-time Setup's block (`setup.py`), not a suite
            continue
        found += [line.strip() for line in block.group(1).splitlines()
                  if line.strip() and not line.strip().startswith("#")]
    if not found:
        raise core.Fail("no-checks: map.md has no command in a Checks code block")
    return found


def run_all(cmds, cwd, log, run=None):
    """Runs each command in `cwd`, timing it, inside run `run`'s environment when given; stops at the first red one.
    Returns the `Suite` results so far."""
    done = []
    for cmd in cmds:
        start = time.monotonic()
        res = core.run(["bash", "-c", in_env(run, cmd) if run else cmd], log, cwd=cwd)
        done.append(Suite(cmd, res.rc, time.monotonic() - start, res.out + res.err))
        log.write(f"suite {cmd!r}: rc {res.rc} in {done[-1].seconds:.1f}s\n")
        log.flush()
        if res.rc != 0:
            break
    return done


def _summary(line):
    """True for a count summary (pytest's `N skipped in Xs`, Jest's `Tests: N skipped, ...`): never a skip row."""
    return bool(PYTEST_SUMMARY.match(line) or JEST_SUMMARY.match(line))


def _repo_skip(line):
    """The reason of one of this repo's `<suite>: skipped (why)` lines, or None."""
    m = REPO_LINE.match(line)
    if not m or _summary(line) or m.group(2).startswith(("ok", "FAILED")):
        return None
    name, rest = m.groups()
    why = re.sub(r",?\s*\bskipped\b[,:]?\s*", " ", rest).strip()
    if why.startswith("(") and why.endswith(")"):
        why = why[1:-1]
    return f"{name}: {why}" if why else name


def _reasons(lines):
    """Every skip reason in a suite's output, in order: this repo's skip lines (inside its framing only), pytest's
    `-rs` lines and unittest's verbose `... skipped 'reason'` lines. Count summaries name no reason."""
    framed = any(map(FRAMING.match, lines))
    found = []
    for line in lines:
        m = PYTEST_SKIP.match(line) or UNITTEST_SKIP.search(line)
        reason = m.group(1) if m else _repo_skip(line) if framed else None
        if reason:
            found.append(reason)
    return found


def _repo(lines):
    if not any(map(FRAMING.match, lines)):
        return None
    found = [m for m in map(REPO_LINE.match, lines) if m and not _summary(m.group(0))]
    if not found:
        return None
    passed = sum(m.group(2).startswith("ok") for m in found)
    failed = sum(m.group(2).startswith("FAILED") for m in found)
    return passed, failed, len(found) - passed - failed


def _pytest(lines):
    totals, seen = {}, False
    for m in filter(None, map(PYTEST_SUMMARY.match, lines)):
        seen = True
        for n, word in re.findall(r"(\d+) (\w+)", m.group(1)):
            totals[word] = totals.get(word, 0) + int(n)
    if not seen:
        return None
    failed = totals.get("failed", 0) + totals.get("error", 0) + totals.get("errors", 0)
    return totals.get("passed", 0), failed, totals.get("skipped", 0)


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
    return passed, failed, skipped


def _jest(lines):
    totals, seen = {}, False
    for m in filter(None, map(JEST_SUMMARY.match, lines)):
        seen = True
        for n, word in re.findall(r"(\d+) (\w+)", m.group(1)):
            totals[word] = totals.get(word, 0) + int(n)
    if not seen:
        return None
    return totals.get("passed", 0), totals.get("failed", 0), totals.get("skipped", 0) + totals.get("todo", 0)


def counts(out):
    """`(passed, failed, skipped, skip_reasons)` read from a suite's output; the counts `None` when it says none.

    Inside this repo's runner framing its suite lines win, since `tests/run.sh` also prints the unittest output of the
    suites it runs; elsewhere a `<word>: ok` line is any program's log, and pytest's summary, then unittest's, then
    Jest's count. Several summaries of one kind (one per folder) are summed. The reasons are `_reasons`: one per
    skipped test, so a reason shared by two tests is there twice.
    """
    lines = out.splitlines()
    for parse in (_repo, _pytest, _unittest, _jest):
        found = parse(lines)
        if found:
            return found + (_reasons(lines),)
    return None, None, None, _reasons(lines)


def save(run, ident, suites):
    """Writes `suites` to `<run>/reports/<ident>.checks.json`, replacing an earlier merge's file."""
    path = core.resolve(run) / "reports" / f"{ident}.checks.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for s in suites:
        passed, failed, skipped, reasons = counts(s.out)
        rows.append({"command": s.command, "rc": s.rc, "passed": passed, "failed": failed, "skipped": skipped,
                     "seconds": round(s.seconds, 1), "skip_reasons": reasons})
    path.write_text(json.dumps(rows, indent=1) + "\n", encoding="utf-8")


def tool(reason):
    """The tool a skip reason names as missing (`zsh is not installed`, `requires docker`), or None: a word that
    names no tool (`no such file`, `install a newer one`) is not one."""
    for pattern in (TOOL_BEFORE, TOOL_AFTER):
        for m in pattern.finditer(reason):
            if m.group(1).lower() not in NOT_TOOLS:
                return m.group(1)
    return None


def skip_reasons(text):
    """The skips in a suite's output as `Skip(tool, reason)`, in order: `counts`'s reasons, each reason once."""
    found = []
    for reason in dict.fromkeys(counts(text)[3]):
        found.append(Skip(tool(reason), reason))
    return found
