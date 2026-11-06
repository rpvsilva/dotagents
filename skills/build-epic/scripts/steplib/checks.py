"""`map.md`'s `Checks`: the suite commands a merge runs in the task's worktree, each timed.

The commands are the lines inside the fenced code blocks of the `## Checks` section, blank lines and `#` comments
skipped. Each runs under `bash -c` in the worktree, its output in the step's log.
"""
import re
import time
from collections import namedtuple

from . import core

Suite = namedtuple("Suite", "command rc seconds")


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
        done.append(Suite(cmd, res.rc, time.monotonic() - start))
        log.write(f"suite {cmd!r}: rc {res.rc} in {done[-1].seconds:.1f}s\n")
        log.flush()
        if res.rc != 0:
            break
    return done
