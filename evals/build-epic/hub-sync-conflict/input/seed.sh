#!/usr/bin/env bash
# Gives the repo its tickets' history: ticket 02's branch `feat/report`, cut from `main` and holding task 02-01 as a
# merge commit, then ticket 01 (`stats`) merged into `main`. Both add their command to the same line of
# `tally/commands.py`, so a hub task of ticket 02 that merges `main` conflicts there. Records 02-01's `Commit:`.
# Usage: seed.sh <work> <run>
set -euo pipefail
work="$1" run="$2"
g() { git -C "$work" "$@"; }
g config user.name build-epic
g config user.email build-epic@localhost
g checkout -q -b feat/report main
g checkout -q -b task/report/01
cat > "$work/tally/report.py" <<'PY'
def run(items):
    open_items = [i for i in items if not i["done"]]
    return [f"report: {len(open_items)} open"] + ["- " + i["title"] for i in open_items]
PY
cat > "$work/tests/report_test.py" <<'PY'
from tally import report


def test_report_lists_open_items():
    assert report.run([{"title": "a", "done": True}, {"title": "b", "done": False}]) == ["report: 1 open", "- b"]
PY
printf '"""The command names `python -m tally` accepts, sorted."""\nCOMMANDS = ["add", "list", "report"]\n' > "$work/tally/commands.py"
g add -A && g commit -qm "feat: report command lists open items"
task="$(g rev-parse HEAD)"
g checkout -q feat/report
merged="$(g commit-tree "$task^{tree}" -p feat/report -p "$task" -m "Merge task 02-01: report command")"
g update-ref refs/heads/feat/report "$merged"
g checkout -q main
g branch -q -D task/report/01
cat > "$work/tally/stats.py" <<'PY'
def summary(items):
    return f"{sum(1 for i in items if i['done'])} of {len(items)} done"


def run(items):
    return [summary(items)]
PY
cat > "$work/tests/stats_test.py" <<'PY'
from tally import stats


def test_summary_counts_done_items():
    assert stats.summary([{"title": "a", "done": True}, {"title": "b", "done": False}]) == "1 of 2 done"
PY
printf '"""The command names `python -m tally` accepts, sorted."""\nCOMMANDS = ["add", "list", "stats"]\n' > "$work/tally/commands.py"
g add -A && g commit -qm "feat: stats command (ticket 01)"
g reset -q --hard
sed -i.bak "s/^Commit: .*/Commit: $merged/" "$run/tasks/02-01-report-command.md" && rm "$run/tasks/02-01-report-command.md.bak"
