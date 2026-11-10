#!/usr/bin/env python3
"""Print the eval cases a change affects, one `<skill>/<case>` per line, the way `tests/affected.sh` picks suites.

  python3 -I evals/pick.py [--all | --base <ref> | -- <path>...]

The changed files come from `tests/affected.sh --files` with the same arguments. A file under `skills/<s>/` (but not
a `test_*` file or one under `fixtures/`) or under `evals/<s>/` picks `<s>`'s cases. `skills/_shared/conventions.md`,
`evals/_runner/*`, `evals/*.py`, `--all` and a change with no merge base pick every case. A changed
`skills/_shared/settings.json` picks the skills whose `settings.json` opts in to a changed key with `{"shared": true}`;
when the changed keys cannot be read (no merge base, a missing or broken file), every case. A change touching no
skill with cases prints nothing. Exits 0, or non-zero when `tests/affected.sh` refuses the arguments.
"""
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
EVALS = REPO / "evals"
SHARED = "skills/_shared/settings.json"
USAGE = "usage: evals/pick.py [--all | --base <ref> | -- <path>...]\n"


def every_case():
    """`{skill: [case, ...]}` for every case folder (one holding `case.json`) under `evals/`."""
    found = {}
    for skill in sorted(EVALS.iterdir()):
        if skill.is_dir() and not skill.name.startswith(("_", ".")):
            names = [c.name for c in sorted(skill.iterdir()) if (c / "case.json").is_file()]
            if names:
                found[skill.name] = names
    return found


def changed_files(args):
    """The changed files `tests/affected.sh --files` prints, or None when every case runs."""
    got = subprocess.run(["bash", str(REPO / "tests" / "affected.sh"), "--files", *args], cwd=REPO,
                         capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if got.returncode:
        sys.stderr.write(USAGE if got.returncode == 2 else got.stderr)
        sys.exit(got.returncode)
    files = got.stdout.splitlines()
    return None if "--all" in files else files


def git(*args):
    got = subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    return got.stdout if got.returncode == 0 else None


def changed_shared_keys(args):
    """The keys of `skills/_shared/settings.json` added, removed or changed since the merge base, or None when that
    cannot be read."""
    base = args[1] if args[:1] == ["--base"] else "origin/main"
    since = git("merge-base", base, "HEAD")
    old = since and git("show", f"{since.strip()}:{SHARED}")
    try:
        old, new = json.loads(old), json.loads((REPO / SHARED).read_text())
    except (TypeError, ValueError, OSError):
        return None
    if not isinstance(old, dict) or not isinstance(new, dict):
        return None
    return {k for k in old.keys() | new.keys() if old.get(k) != new.get(k)}


def opts_in(skill, keys):
    try:
        data = json.loads((REPO / "skills" / skill / "settings.json").read_text())
    except (ValueError, OSError):
        return False
    return isinstance(data, dict) and any(data.get(k) == {"shared": True} for k in keys)


def skill_of(path):
    """The skill whose cases `path` picks, `"*"` for every skill, or None."""
    parts = path.split("/")
    if path == "skills/_shared/conventions.md" or (parts[0] == "evals" and (
            len(parts) == 2 and path.endswith(".py") or len(parts) > 2 and parts[1] == "_runner")):
        return "*"
    if len(parts) < 3 or parts[0] not in ("skills", "evals") or parts[1].startswith(("_", ".")):
        return None
    if parts[0] == "skills" and ("fixtures" in parts[2:-1] or parts[-1].startswith("test_")):
        return None
    return parts[1]


def pick(args):
    cases = every_case()
    files = changed_files(args)
    skills = set(cases) if files is None else set()
    for path in files or []:
        if path == SHARED:
            keys = changed_shared_keys(args)
            skills |= set(cases) if keys is None else {s for s in cases if opts_in(s, keys)}
        elif skill_of(path) == "*":
            skills |= set(cases)
        elif skill_of(path) in cases:
            skills.add(skill_of(path))
    return [f"{s}/{c}" for s in sorted(skills) for c in cases[s]]


if __name__ == "__main__":
    for case in pick(sys.argv[1:]):
        print(case)
