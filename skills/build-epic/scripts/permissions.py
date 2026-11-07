#!/usr/bin/env python3
"""Lists the permission rules a run needs, for the approval message. It only lists them: no agent writes permission
settings.

Usage: python3 -I permissions.py <run>

Prints one allow rule per line: one per step script that merges or posts reviews (`merge_task.py`, `post_review.py`,
`ship_ticket.py`, by their installed path), `Bash(capped stop *)`, and `Bash(git push --force-with-lease origin <base>)`
when `decisions.md` has a `base:` other than the repo's default branch (`--into` mode): it names that branch only, so
no rule allows a push to the default branch. A `base:` holding a space or a wildcard fails `bad-value`. With `merge_identity: human` it
ends with one line saying merges run as the human's own login. Like `status.py`, it prints several stdout lines.
Exits 0, or prints `FAIL run <reason>` and exits 1.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from steplib import core, decisions, runfolder  # noqa: E402

INSTALLED = "~/.agents/skills/build-epic/scripts"
STEP_SCRIPTS = ("merge_task.py", "post_review.py", "ship_ticket.py")
# A branch name a rule can hold literally: no space, no wildcard, no refspec (`:`, `+`), no leading `-`.
BRANCH = re.compile(r"[A-Za-z0-9._/][A-Za-z0-9._/-]*")


def rules(run):
    d = decisions.load(run) or decisions.Decisions()
    out = [f"Bash(python3 -I {INSTALLED}/{script} *)" for script in STEP_SCRIPTS]
    out.append("Bash(capped stop *)")
    base = d.get("base")
    if base and base != runfolder.run_info(run)["Default"]:
        if not BRANCH.fullmatch(base):
            raise core.Fail(f"bad-value: base {base!r} is not a plain branch name")
        out.append(f"Bash(git push --force-with-lease origin {base})")
    if d.get("merge_identity") == "human":
        who = d.get("human")
        out.append("Merges run as the human's own login" + (f" ({who})" if who else "") + ", not an App's.")
    return out


def main(argv):
    if len(argv) != 1:
        print("FAIL run usage: permissions.py <run>")
        return 2
    run = core.resolve(argv[0])
    if not run.is_dir():
        print(f"FAIL run no-run-folder: {run}")
        return 1
    try:
        lines = rules(run)
    except core.Fail as e:
        print(f"FAIL run {e.reason}")
        return 1
    for line in lines:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
