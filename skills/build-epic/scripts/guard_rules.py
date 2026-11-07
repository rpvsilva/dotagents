#!/usr/bin/env python3
"""Lists the repo's leave-for-human merge guard rules, so step 5 copies each rule's name as its Authorizations clause.

Usage: python3 -I guard_rules.py <run>

Fetches the run's base (`base:` in `decisions.md`, else the repo's default branch) and prints one line per rule of the
`agent-merge-guard` block on `origin/<base>` (format: `../merge-guard.md`): `rule <name> <kind> <pattern>`, the pattern
as written. A repo with no block prints nothing. Like `permissions.py`, it prints several stdout lines; git's output
goes to `<run>/logs/run-guard_rules.log`. Exits 0, or prints `FAIL run <reason>` and exits 1:
- `no-run-folder`, `no-run-md`, `run-md-missing`: the run folder is not initialised.
- `git-fetch`: the base could not be fetched.
- `guard-no-base: origin/<base>`: the base is not on `origin`.
- `bad-rule: line <n>: <text>`: a block line is no rule (or reuses a name), or `bad-rule: unclosed ...` block.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from steplib import core, decisions, gitops, guard, runfolder  # noqa: E402


def rules(run, log):
    info = runfolder.run_info(run)
    repo = core.resolve(info["Repo"])
    d = decisions.load(run)
    base = (d.get("base") if d else None) or info["Default"]
    gitops.Git(repo, log).fetch("origin", base)
    return guard.load(repo, f"origin/{base}", log)


def main(argv):
    if len(argv) != 1:
        print("FAIL run usage: guard_rules.py <run>")
        return 2
    run = core.resolve(argv[0])
    if not run.is_dir():
        print(f"FAIL run no-run-folder: {run}")
        return 1
    (run / "logs").mkdir(exist_ok=True)
    try:
        with open(run / "logs" / "run-guard_rules.log", "a", encoding="utf-8") as log:
            found = rules(run, log)
    except core.Fail as e:
        print(f"FAIL run {e.reason}")
        return 1
    for rule in found:
        print(f"rule {rule.name} {rule.kind} {rule.pattern}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
