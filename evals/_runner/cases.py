#!/usr/bin/env python3
"""Eval cases: load `evals/<skill>/<case>/` and check it against the case format (docs/evals.md).

`python3 -I evals/_runner/cases.py check [<evals dir>]` prints `ok`, or one line per problem (`<case path>: <problem>`)
and exits 1. Other modules call `load(case_dir)`, which returns the case with its defaults filled in.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PRICING = REPO / "skills" / "telemetry" / "pricing.json"
KEYS = ("prompt", "model", "runs", "caps", "settings", "install", "rubric", "judge_model")
CAPS = {"turns": int, "secs": float, "cost_usd": float}
DEFAULTS = {"prompt": "prompt.md", "runs": 3, "settings": {}, "judge_model": "claude-haiku-5-5"}
BUILT_IN_CHECKS = ("no-github",)


class CaseError(ValueError):
    """A malformed case; `problems` holds one line each."""

    def __init__(self, case_dir, problems):
        super().__init__("\n".join(f"{case_dir}: {p}" for p in problems))
        self.problems = problems


def priced_models():
    return set(json.loads(PRICING.read_text())["models"])


def part_names():
    """Every part `scripts/parts.sh list` prints."""
    out = subprocess.run(["bash", str(REPO / "scripts" / "parts.sh"), "list"], capture_output=True, text=True,
                         stdin=subprocess.DEVNULL, check=True).stdout
    return {line.split("\t")[0] for line in out.splitlines() if line}


def alias_problem(field, model, priced):
    """Spec open question 13: a full ID holds a digit, does not end in `latest`, and a `claude-*` one is priced."""
    if not isinstance(model, str) or not model:
        return f"{field} must be a model ID"
    if not re.search(r"\d", model) or model.endswith("latest") or (model.startswith("claude-") and model not in priced):
        return f"{field} '{model}' is an alias, not a full model ID"
    return None


def positive(value, kind):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return False
    return kind is float or isinstance(value, int)


def install_problems(install, parts):
    """An eval home installs only named parts, never with no selection (install.sh would install every part) and
    never `global`."""
    if not isinstance(install, list) or not all(isinstance(p, str) and p for p in install):
        return ["install must be a list of part names"]
    if not install:
        return ["install must name at least one part"]
    if "global" in install:
        return ["install must not name global"]
    return [f"install names unknown part '{p}'" for p in install if p not in parts]


def field_problems(data, priced, parts):
    out = [f"unknown key '{k}' in case.json" for k in data if k not in KEYS]
    if "model" not in data:
        out.append("model missing in case.json")
    else:
        out.append(alias_problem("model", data["model"], priced))
    if "judge_model" in data:
        out.append(alias_problem("judge_model", data["judge_model"], priced))
    if "runs" in data and not positive(data["runs"], int):
        out.append("runs must be a positive integer")
    caps = data.get("caps")
    if not isinstance(caps, dict):
        out.append("caps must be an object with turns, secs and cost_usd")
    else:
        out += [f"unknown key 'caps.{k}' in case.json" for k in caps if k not in CAPS]
        for name, kind in CAPS.items():
            if name not in caps:
                out.append(f"caps.{name} missing in case.json")
            elif not positive(caps[name], kind):
                out.append(f"caps.{name} must be a positive {'integer' if kind is int else 'number'}")
    if "settings" in data and not isinstance(data["settings"], dict):
        out.append("settings must be an object")
    if "install" in data:
        out += install_problems(data["install"], parts)
    for field in ("prompt", "rubric"):
        if field in data and (not isinstance(data[field], str) or not data[field]):
            out.append(f"{field} must be a file name")
    return [p for p in out if p]


def fixture_problems(fixture):
    if not fixture.is_dir():
        return ["fixture/ missing"]
    out = []
    for root, dirs, files in os.walk(fixture):
        rel = Path(root).relative_to(fixture)
        for name in sorted(dirs + files):
            if name == ".git":
                out.append(f"fixture/ holds {(rel / name).as_posix()}")
            elif name.startswith("test_") and name.endswith(".py"):
                out.append(f"fixture/ holds {(rel / name).as_posix()}")
        dirs[:] = sorted(d for d in dirs if d != ".git")
    return out


def checks_of(case_dir):
    """Returns (checks, problems): the case's checks by stem, every executable file in `checks/`. Two files with
    one stem, or a stem a built-in check uses, are problems."""
    folder = case_dir / "checks"
    if not folder.is_dir():
        return {}, []
    checks, problems = {}, []
    for f in sorted(folder.iterdir()):
        if not (f.is_file() and os.access(f, os.X_OK)):
            continue
        if f.stem in BUILT_IN_CHECKS:
            problems.append(f"check name '{f.stem}' is reserved for the built-in check")
        elif f.stem in checks:
            if f"duplicate check name '{f.stem}'" not in problems:
                problems.append(f"duplicate check name '{f.stem}'")
        else:
            checks[f.stem] = f
    return checks, problems


def read(case_dir, priced=None, parts=None):
    """Returns (case, problems): the case with defaults filled in, or None when case.json cannot be read."""
    case_dir = Path(case_dir)
    manifest = case_dir / "case.json"
    if not manifest.is_file():
        return None, ["case.json missing"]
    try:
        data = json.loads(manifest.read_text())
    except (ValueError, UnicodeDecodeError) as err:
        return None, [f"case.json is not valid JSON ({err})"]
    if not isinstance(data, dict):
        return None, ["case.json must be an object"]
    skill = case_dir.parent.name
    problems = field_problems(data, priced_models() if priced is None else priced,
                              part_names() if parts is None else parts)
    case = {**DEFAULTS, "install": [skill], **data, "skill": skill, "case": case_dir.name, "dir": case_dir}
    if not (REPO / "skills" / skill / "SKILL.md").is_file():
        problems.append(f"no skills/{skill}/SKILL.md")
    if isinstance(case["prompt"], str) and case["prompt"]:
        prompt = case_dir / case["prompt"]
        if not prompt.is_file():
            problems.append(f"prompt file {case['prompt']} missing")
        elif not prompt.read_text().strip():
            problems.append(f"prompt file {case['prompt']} is empty")
    if "rubric" not in data:
        case["rubric"] = "rubric.md" if (case_dir / "rubric.md").is_file() else None
    elif isinstance(data["rubric"], str) and data["rubric"] and not (case_dir / data["rubric"]).is_file():
        problems.append(f"rubric file {data['rubric']} missing")
    case["fixture"] = case_dir / "fixture"
    problems += fixture_problems(case["fixture"])
    case["input"] = case_dir / "input" if (case_dir / "input").is_dir() else None
    case["checks"], check_problems = checks_of(case_dir)
    problems += check_problems
    if not case["checks"] and not check_problems:
        problems.append("no executable in checks/")
    return case, problems


def load(case_dir):
    """The case at `case_dir` with its defaults filled in; raises CaseError when it is malformed."""
    case, problems = read(case_dir)
    if problems:
        raise CaseError(case_dir, problems)
    return case


def check(evals_dir):
    """One `<path>: <problem>` line per problem under `evals_dir`; `evals/_*` folders are tooling, skipped."""
    evals_dir = Path(evals_dir)
    if not evals_dir.is_dir():
        return []
    priced, parts = priced_models(), part_names()
    lines = []
    for skill_dir in sorted(evals_dir.iterdir()):
        if not skill_dir.is_dir() or skill_dir.name.startswith(("_", ".")):
            continue
        if not (REPO / "skills" / skill_dir.name / "SKILL.md").is_file():
            lines.append(f"{skill_dir}: names no skill (no skills/{skill_dir.name}/SKILL.md)")
            continue
        for case_dir in sorted(skill_dir.iterdir()):
            if case_dir.is_dir():
                lines += [f"{case_dir}: {p}" for p in read(case_dir, priced, parts)[1]]
    return lines


def main(argv):
    if not argv or argv[0] != "check" or len(argv) > 2:
        print("usage: cases.py check [<evals dir>]", file=sys.stderr)
        return 2
    evals_dir = Path(argv[1]) if len(argv) == 2 else Path(os.path.relpath(REPO / "evals"))
    lines = check(evals_dir)
    print("\n".join(lines) if lines else "ok")
    return 1 if lines else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
