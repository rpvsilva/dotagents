"""A run's checks: each executable of the case's `checks/`, then the built-in `no-github` check.

`run(checks, paths)` runs every check with cwd `paths["work"]` (the fixture repo's end state) and an env holding only
`PATH`, `HOME` and its config folders, `LANG`, `TMPDIR` and the `EVAL_*` paths (spec open question 15): `EVAL_WORK`,
`EVAL_SCRATCH`, `EVAL_INPUT`, `EVAL_HOME`, `EVAL_TRANSCRIPT`, `EVAL_GH_CALLS` and `EVAL_CHECKOUT`. No auth var
reaches a check. A check passes on exit 0; one that cannot start, or outlives `secs`, fails. `no-github` passes when
the `gh` stub recorded no call. Used by `run.py` on a fresh run and by grading on a kept one.
"""
import hashlib
import subprocess
from pathlib import Path

import home

NO_GITHUB = "no-github"
SECS = 300
# The `paths` keys, each given to a check as EVAL_<KEY upper-cased>.
KEYS = ("work", "scratch", "input", "home", "transcript", "gh_calls", "checkout")


def env(paths):
    out = home.tool_env(paths["home"], ())
    out.update({"EVAL_" + key.upper(): str(paths[key]) for key in KEYS})
    return out


def run_one(path, paths, secs=SECS):
    try:
        proc = subprocess.run([str(path)], cwd=str(paths["work"]), env=env(paths), stdin=subprocess.DEVNULL,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=secs)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def gh_called(gh_calls):
    try:
        return Path(gh_calls).read_text().strip() != ""
    except OSError:
        return False


def run(checks, paths, secs=SECS):
    """`{name: passed}` for `checks` (`{name: path}`, as `cases.load()` gives them) and `no-github`."""
    out = {name: run_one(path, paths, secs) for name, path in sorted(checks.items())}
    out[NO_GITHUB] = not gh_called(paths["gh_calls"])
    return out


def checks_hash(case_dir):
    """sha256 over the files of the case's `checks/`: each name and the sha256 of its contents, in name order."""
    folder = Path(case_dir) / "checks"
    h = hashlib.sha256()
    files = sorted(p for p in folder.rglob("*") if p.is_file()) if folder.is_dir() else []
    for path in files:
        name = path.relative_to(folder).as_posix().encode("utf-8")
        h.update(name + b"\0" + hashlib.sha256(path.read_bytes()).digest())
    return h.hexdigest()
