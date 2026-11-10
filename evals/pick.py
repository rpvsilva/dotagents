#!/usr/bin/env python3
"""Print the eval cases a change affects, one `<skill>/<case>` per line, the way `tests/affected.sh` picks suites.

  python3 -I evals/pick.py [--all | --base <ref> | -- <path>...]

The changed files come from `tests/affected.sh --files` with the same arguments. A file under `skills/<s>/` (but not a
`test_*` file or one under `fixtures/`) or under `evals/<s>/` picks `<s>`'s cases. `skills/_shared/conventions.md`,
`evals/_runner/*` (but not a file under `fixtures/`), `evals/*.py`, `--all` and a change with no merge base pick every
case; a `test_*` file under `evals/`, at any depth, picks none. A changed `skills/_shared/settings.json` picks the skills whose `settings.json` opts in to a
changed key with `{"shared": true}`, and `configure`, which shows and changes every Setting; when the changed keys
cannot be read (no merge base, a missing or broken file), every case. A change touching no skill with cases prints
nothing.
A case is also picked by a change to a file a part ships (`scripts/parts.sh files`, so a skill's
`adapters/<tool>/<skill>/` helpers count) when that part is in the `scripts/parts.sh closure --optional` of its skill
and its `install` parts. A core part's file counts only when that part is named there, or when the skill's own shipped
files name the file by its repo path or its installed path (`~/.agents/bin/settings` for `scripts/settings.sh`,
`~/.agents/hooks/<x>` for `scripts/hooks/<x>`). A case whose parts cannot be read is picked by any change.
Exits 0, or non-zero when `tests/affected.sh` refuses the arguments.
"""
import functools
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
EVALS = REPO / "evals"
SHARED = "skills/_shared/settings.json"
CONFIGURE = "configure"
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


def parts(*args):
    """The lines `scripts/parts.sh` prints, or None when it fails."""
    got = subprocess.run(["bash", str(REPO / "scripts" / "parts.sh"), *args], cwd=REPO, capture_output=True,
                         text=True, stdin=subprocess.DEVNULL)
    return got.stdout.splitlines() if got.returncode == 0 else None


@functools.lru_cache(maxsize=None)
def core():
    listed = parts("list") or []
    return {"_shared"} | {line.split("\t")[0] for line in listed if line.split("\t")[2:3] == ["core"]}


@functools.lru_cache(maxsize=None)
def shipped(part):
    """The files `part` ships that can change how a run goes: no `test_*` file and none under `fixtures/`."""
    files = parts("files", part)
    if files is None:
        return None
    return {f for f in files if not ("fixtures" in f.split("/")[:-1] or f.split("/")[-1].startswith("test_"))}


INSTALLED = {"scripts/settings.sh": "~/.agents/bin/settings", "scripts/capped": "~/.agents/bin/capped"}


def installed(path):
    """Where `install.sh` links the core file `path`, or None."""
    if path.startswith("scripts/hooks/"):
        return "~/.agents/hooks/" + path[len("scripts/hooks/"):]
    if path.startswith("skills/"):
        return "~/.agents/" + path
    return INSTALLED.get(path)


@functools.lru_cache(maxsize=None)
def text_of(skill):
    """The text of the files `skill` ships, or None when they cannot be read."""
    files = shipped(skill)
    if files is None:
        return None
    texts = []
    for f in sorted(files):
        try:
            texts.append((REPO / f).read_text(errors="replace"))
        except OSError:
            pass
    return "\n".join(texts)


def named_by(skill, files):
    """The core `files` the files `skill` ships name by their repo path or installed path, or None. `SHARED` is left
    out: its own rule picks by the keys a skill opts in to."""
    text = text_of(skill)
    if text is None:
        return None
    return {f for f in files - {SHARED} if f in text or (installed(f) or "\0") in text}


def case_files(skill, case):
    """The files the parts of `skill`'s `case` ship, or None when they cannot be read."""
    try:
        install = json.loads((EVALS / skill / case / "case.json").read_text()).get("install", [skill])
    except (ValueError, OSError, AttributeError):
        return None
    if not isinstance(install, list) or not all(isinstance(p, str) and p and not p.startswith("-") for p in install):
        return None
    named = {skill, *install}
    closure = parts("closure", "--optional", *sorted(named))
    if closure is None:
        return None
    files = set()
    for part in closure:
        got = shipped(part)
        if got is not None and part not in named and part in core():
            got = named_by(skill, got)
        if got is None:
            return None
        files |= got
    return files


def skill_of(path):
    """The skill whose cases `path` picks, `"*"` for every skill, or None."""
    parts = path.split("/")
    if parts[0] == "evals" and (parts[-1].startswith("test_") or
                                parts[1:2] == ["_runner"] and "fixtures" in parts[2:-1]):
        return None
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
            skills |= set(cases) if keys is None else {s for s in cases if opts_in(s, keys) or keys and s == CONFIGURE}
        elif skill_of(path) == "*":
            skills |= set(cases)
        elif skill_of(path) in cases:
            skills.add(skill_of(path))
    picked = []
    for s in sorted(cases):
        for c in cases[s]:
            if s not in skills and files:
                found = case_files(s, c)
                if found is not None and not found & set(files):
                    continue
            elif s not in skills:
                continue
            picked.append(f"{s}/{c}")
    return picked

if __name__ == "__main__":
    for case in pick(sys.argv[1:]):
        print(case)
