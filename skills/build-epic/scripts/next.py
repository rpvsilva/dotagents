#!/usr/bin/env python3
"""Says what the orchestrator does next, read from the run folder alone: build-epic's state machine.

Usage: python3 -I next.py <run>
       python3 -I next.py <run> --go
       python3 -I next.py <run> --returned <TT-NN> [--title <subject>] [< message]
       python3 -I next.py <run> --reviewed <TT> <output> [--base <ref>] [--note <text>]
       python3 -I next.py <run> --triaged <TT> fix|none

Prints one line per action, most urgent first, each `<action> <id> [details]  (<step file>)`, the step file under
`skills/build-epic/steps/` holding how to do it; a build action's note also carries the one instruction it needs,
`(<step file>: <instruction>)` (`HOW`), so the step file is read only for a `FAIL` or a case it does not cover. A run that is not built yet prints its one next setup action:

  init run            (no `run.md`)                         setup.md
  rules run           (`decisions.md` lacks `merge_identity:` or `review_before_agent_merge:`)   setup.md
  plan A              (no `spec.md` or `areas.md`)          plan.md
  explore <area>      (one per `## <area>` of `areas.md` with no note, up to the free slots)     plan.md
  plan B              (no `map.md` or no tasks)             plan.md
  plan revise         (`plan_check.py` finds problems)      plan.md
  setup run           (`decisions.md` lacks `test_command:`)                                       setup.md
  approve run         (`decisions.md` lacks `approved: yes`)                                       approve.md

Once `approved: yes` is recorded, only `run.md` is checked again: a re-plan runs its own `plan_check.py`.

A plan agent whose output says `blocked` gives `human <id> blocked: <why>` instead. Once approved, every line of the
build at once, in this order:

  human <TT-NN> <blocked|plan-wrong>: <why>   a task waits on the human                  build.md
  human <TT> review-round-3                   a ticket's review still asks for a fix after two rounds   ship.md
  finish <TT-NN>       an in-progress task with a fresh output and no `Finished:` line    build.md
  review <TT-NN>       a finished `Tier: high` task with no review of its head            build.md
  merge <TT-NN>        a finished task, reviewed when high tier (one per ticket); a review whose findings
                       file `reviews/<TT-NN>-<sha7>-findings.md` exists waits for its fix instead      build.md
  start <TT>           a `ready` ticket whose `Blocked by` tickets are all `merged`        build.md
  dispatch <TT-NN> [hub]   a ready task of a building ticket: blockers done, Writes disjoint from every task
                           in flight or dispatched above, while a slot is free             build.md
  ship <TT> open|review|triage|re-review|merge   a building ticket whose tasks are all done          ship.md
  wait <id> [why]      a task in flight with no output yet, or a ticket waiting on a human or another PR
  clean run / report run   once nothing is left to build or start                        finish.md

The slots are `decisions.md`'s `parallel:` (default 1); every in-progress task without an output holds one. A
ticket's review state comes from `<run>/logs/PR<n>-review.json` (the last review posted, `commit_id` its head), its
`Triage:` line and its `-review-fixes` task. `--triaged <TT> fix|none` records the orchestrator's sorting of the
review at the ticket branch's tip on that line (`Triage: <sha7> fix|none, ...`) and prints `OK <TT> triaged <sha7>
<fix|none>`. Exits 0; `FAIL run no-run-folder` and exit 1 when the folder is missing.

`--go` does, once the run is approved, every action that needs no decision, then prints what is left. It repeats
until nothing more moves: `start` (`start_ticket.py`), `finish` (`finish_task.py`, the commit titled as the task),
`dispatch` (`start_task.py`, then `task_prompt.py`, `--remote` for a task with no sync conflict when
`decisions.md` says `remote: on`, as `setup.py` records it; `--remote` or `--local` given to any of these calls
overrides it), `merge` (`merge_task.py`) once no agent waits to be started, and a ticket's `ship open` and `ship merge`
(`ship_ticket.py`). Each script's line is printed as it ran.
A prepared task prints `agent <TT-NN> prompt=<file> description=<text>` (start an `epic-implementer` with the file's
text) or `remote <TT-NN> args=<arguments>` (run `remote-agent` with them in the background, its stdout to
`<run>/reports/<TT-NN>.json`); then the actions left, as without `--go`. A script's `FAIL` stops that action and is
printed with its log; its step file says what to do.

`--returned <TT-NN>` is `--go` after a task's agent returned: `report_check.py` on the final message (stdin; for a
task whose latest prompt was a remote one, the saved output, `--from-report`, and stdin is not read), `capped stop` on its worktree when it ran here, `finish_task.py`
(`--title`, when given, is the commit subject), then `--go`. A first `brief-violation` writes the reissue prompt
(`task_prompt.py --reissue`) and prints it as an `agent` or `remote` line; a second prints the `FAIL` alone.

`--reviewed <TT> <output>` is `--go` after a ticket's reviewer returned: `post_review.py` posts the output on the
ticket's PR (`--base`, default `origin/<the run's base>`; `--note` passes through) and prints its line, then the
output rendered (`render.py`, `security` for a `-security.json` output) for the triage, then `--go`.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import status  # noqa: E402
from steplib import ci, decisions, runfolder, taskwt  # noqa: E402

PLAN_CHECK = Path(__file__).resolve().parents[1] / "plan_check.py"
DONE_LIKE = ("merged", "in-review", "claimed")


def git(repo, *args):
    p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, stdin=subprocess.DEVNULL)
    return p.stdout.strip() if p.returncode == 0 else None


def output(run, ident):
    """The agent output `reports/<id>.json` as a dict, or None."""
    try:
        value = json.loads((run / "reports" / f"{ident}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def blocked_plan(run, ident):
    out = output(run, ident)
    if out and out.get("status") in ("blocked", "plan-wrong"):
        return f"human {ident} {out['status']}: {out.get('why') or 'no why'}  (plan.md)"
    return None


def areas_to_explore(run):
    text = (run / "areas.md").read_text(encoding="utf-8")
    if re.search(r"no exploration needed", text, re.IGNORECASE):
        return []
    slugs = re.findall(r"^## +(\S+)\s*$", text, re.MULTILINE)
    return [s for s in slugs if not (run / "notes" / f"{s}.md").is_file()]


def setup_action(run, d, slots):
    """The one setup action still to do, as a list of lines, or [] once the plan is approved."""
    get = (lambda k: d.get(k)) if d else (lambda k: None)
    if get("approved") == "yes" and (run / "run.md").is_file():
        return []  # a re-plan after approval runs its own plan_check.py (plan.md)
    if not (run / "run.md").is_file():
        return ["init run  (setup.md)"]
    if not get("merge_identity") or not get("review_before_agent_merge"):
        return ["rules run  (setup.md)"]
    if not (run / "spec.md").is_file() or not (run / "areas.md").is_file():
        return [blocked_plan(run, "plan-A") or "plan A  (plan.md)"]
    missing = areas_to_explore(run)
    if missing:
        stuck = [blocked_plan(run, f"explore-{a}") for a in missing]
        return [s for s in stuck if s] or [f"explore {a}  (plan.md)" for a in missing[:slots]]
    if not (run / "map.md").is_file() or not list((run / "tasks").glob("*.md")):
        return [blocked_plan(run, "plan-B") or "plan B  (plan.md)"]
    check = subprocess.run([sys.executable, "-I", str(PLAN_CHECK), str(run)], capture_output=True, text=True)
    if not check.stdout.startswith("plan ok"):
        problems = len([line for line in check.stdout.splitlines() if line.strip()])
        return [f"plan revise problems={problems}  (plan.md)"]
    if not get("test_command"):
        return ["setup run  (setup.md)"]
    if get("approved") != "yes":
        return ["approve run  (approve.md)"]
    return []


def triage(text):
    """The ticket's `Triage:` entries as [(sha7, fix|none)]."""
    return re.findall(r"([0-9a-f]{7,40}) (fix|none)", runfolder.header(text, "Triage") or "")


def ship_action(run, r, tt, repo):
    """The ship action of a building ticket whose tasks are all done."""
    ident, path, text = r.tickets[tt]
    pr = ci.pr_number(runfolder.header(text, "PR"))
    if not pr:
        return f"ship {ident} open  (ship.md)"
    branch = runfolder.header(text, "Branch")
    tip = git(repo, "rev-parse", branch) if branch else None
    try:
        posted = json.loads((run / "logs" / f"PR{pr}-review.json").read_text(encoding="utf-8")).get("commit_id")
    except (OSError, ValueError, AttributeError):
        posted = None
    rounds = triage(text)
    if not posted:
        return f"ship {ident} review pr={pr}  (ship.md)"
    if tip and not tip.startswith(posted[:7]) and not posted.startswith(tip[:7]):
        return f"ship {ident} re-review pr={pr}  (ship.md)"
    last = rounds[-1] if rounds and posted.startswith(rounds[-1][0][:7]) else None
    if last is None:
        if sum(1 for _, kind in rounds if kind == "fix") >= 2:
            return f"human {ident} review-round-3 pr={pr}  (ship.md)"
        return f"ship {ident} triage pr={pr} head={posted[:7]}  (ship.md)"
    if last[1] == "none":
        return f"ship {ident} merge pr={pr}  (ship.md)"
    return f"wait {ident} review-fixes  (ship.md)"


def build_actions(run, d, slots):
    r = status.Run(run)
    try:
        repo = runfolder.run_info(run)["Repo"]
    except Exception:  # noqa: BLE001 - a run with no run.md has no repo; the git reads below then give None
        repo = None
    order = sorted(r.tickets, key=lambda n: (not n.isdigit(), int(n) if n.isdigit() else 0, n))
    human, finish, review, merge, start, dispatch, ship, wait = [], [], [], [], [], [], [], []
    busy_writes, held = set(), 0
    for tt in order:
        for ident, path, text in r.tasks.get(tt, {}).values():
            if r.status(text) != "in-progress":
                continue
            busy_writes |= runfolder.plan_check.names(runfolder.header(text, "Writes"))
            reported, _, fresh = r.why(ident)
            if not (reported and fresh) and not runfolder.header(text, "Finished"):
                held += 1
    free = max(slots - held, 0)
    for tt in order:
        ident_tt, _, ttext = r.tickets[tt]
        tstatus = r.status(ttext)
        human += [f"human {i} {s}: {why}  (build.md)" for i, s, why in r.stuck(tt)]
        if tstatus == "ready":
            waits = r.waits_on(tt)
            if waits:
                wait.append(f"wait {ident_tt} on ticket {'+'.join(waits)}")
            else:
                start.append(f"start {ident_tt}  (build.md)")
            continue
        if tstatus == "in-review":
            wait.append(f"wait {ident_tt} human approval")
            continue
        if tstatus != "building":
            continue
        tasks = r.tasks.get(tt, {})
        merging = False
        stuck = {i for i, _, _ in r.stuck(tt)}
        for ident, path, text in tasks.values():
            if r.status(text) != "in-progress" or ident in stuck:
                continue
            reported, _, fresh = r.why(ident)
            if not runfolder.header(text, "Finished"):
                if reported and fresh:
                    finish.append(f"finish {ident}  (build.md)")
                else:
                    wait.append(f"wait {ident} implementer")
                continue
            if (runfolder.header(text, "Tier") or "").strip().lower() == "high":
                head = git(run / "worktrees" / ident, "rev-parse", "--short=7", "HEAD")
                if not head or not (run / "reviews" / f"{ident}-{head}.json").is_file():
                    review.append(f"review {ident}  (build.md)")
                    continue
                if (run / "reviews" / f"{ident}-{head}-findings.md").is_file():
                    wait.append(f"wait {ident} fix")
                    continue
            if not merging:
                merge.append(f"merge {ident}  (build.md)")
                merging = True
        for ident in r.dispatchable(tt):
            text = r.tasks[tt][runfolder.norm(ident.split("-", 1)[1])][2]
            writes = runfolder.plan_check.names(runfolder.header(text, "Writes"))
            if free <= 0 or writes & busy_writes:
                continue
            _, on_tickets = runfolder.blockers(runfolder.header(text, "Blocked by"))
            dispatch.append(f"dispatch {ident}{' hub' if on_tickets else ''}  (build.md)")
            busy_writes |= writes
            free -= 1
        if tasks and all(r.status(t) == "done" for _, _, t in tasks.values()):
            ship.append(ship_action(run, r, tt, repo))
    lines = human + [s for s in ship if s.startswith("human ")] + finish + review + merge + start + dispatch
    lines += [s for s in ship if not s.startswith("human ")] + wait
    if not lines:
        states = {r.status(r.tickets[tt][2]) for tt in order}
        left = [tt for tt in order if r.status(r.tickets[tt][2]) not in DONE_LIKE + ("blocked",)]
        if not left and states:
            lines = ["clean run  (finish.md)", "report run  (finish.md)"]
        else:
            lines = ["report run stuck  (finish.md)"]
    return lines


HOW = {
    "human": "show `view.py <run> task <id>` and ask the one question; for a ticket's review-round-3, its review's link",
    "finish": "`next.py <run> --returned <id>` checks, finishes and merges it",
    "review": "a `pr-reviewer` on `review_prompt.py task --worktree <run>/worktrees/<id> --head <its HEAD> --base "
              "<ticket branch> --run <run> --task <task file> --output <run>/reviews/<id>-<sha7>.json`; post nothing",
    "merge": "`next.py <run> --go` merges it", "start": "`next.py <run> --go` starts it",
    "dispatch": "`next.py <run> --go` prepares it",
    "open": "`next.py <run> --go` opens it", "ship-merge": "`next.py <run> --go` merges it",
    "ship-review": "a `pr-reviewer` on `review_prompt.py ticket --pr <n>` (plus a `security-reviewer` when `tier.py` "
                   "says high), then `next.py <run> --reviewed <id> <output>`",
    "ship-review-remote": "remote is on: a remote `pr-reviewer` as review-pr step 3 **Remotely** says "
                          "(`review_prompt.py ticket --pr <n> --remote <run>/reviews/<n>-<sha7>-remote/`, then "
                          "`remote-agent --name review-<n>-<sha7> --agent <its agent.txt>` in the background), plus a "
                          "`security-<n>-<sha7>` one when `tier.py` says high; `remote-agent result <name>` to "
                          "`<run>/remote/<name>.json`, its report copied to `<run>/reviews/<n>-<sha7>.json`, then "
                          "`next.py <run> --reviewed <id> <output>`",
    "triage": "Fix = a blocker, or a should-fix breaking an acceptance criterion or a written rule; none: "
              "`--triaged <id> none`; else a `<id>-NN-review-fixes` task and `--triaged <id> fix`",
    "re-review": "a `pr-reviewer` on `review_prompt.py re-review --base <head reviewed> --findings <findings file>`, "
                 "then `next.py <run> --reviewed <id> <output> --base <head reviewed>`",
    "re-review-remote": "remote is on: a remote `pr-reviewer` as review-pr step 3 **Remotely** says, on "
                        "`review_prompt.py re-review --base <head reviewed> --findings <findings file> --remote "
                        "<run>/reviews/<n>-<sha7>-remote/`; `remote-agent result <name>` to `<run>/remote/<name>.json`, "
                        "its report copied to `<run>/reviews/<n>-<sha7>.json`, then "
                        "`next.py <run> --reviewed <id> <output> --base <head reviewed>`",
    "clean": "`clean.py <run>`", "report": "`status.py <run> --write`, then show `view.py <run> report`",
}


def how(line, remote=False):
    """`line` with the one instruction its action needs inside its step-file note, so the step file is read only for
    a `FAIL` or a case the instruction does not cover; a ticket review's names the remote way when `remote`."""
    head, sep, note = line.partition("  (")
    if not sep:
        return line
    words = head.split(" ")
    key = words[0]
    if key == "ship" and len(words) > 2:
        key = {"review": "ship-review", "merge": "ship-merge"}.get(words[2], words[2])
    text = (remote and HOW.get(f"{key}-remote")) or HOW.get(key)
    return f"{head}  ({note[:-1]}: {text})" if text else line


def record_triage(run, tt, kind):
    path = runfolder.ticket_file(run, tt)
    text = path.read_text(encoding="utf-8")
    repo = runfolder.run_info(run)["Repo"]
    tip = git(repo, "rev-parse", "--short=7", runfolder.header(text, "Branch") or "HEAD")
    if not tip:
        return f"FAIL {tt} no-branch", 1
    entries = [f"{s} {k}" for s, k in triage(text) if s != tip] + [f"{tip} {kind}"]
    runfolder.set_header(path, "Triage", ", ".join(entries))
    return f"OK {tt} triaged {tip} {kind}", 0


SCRIPTS = Path(__file__).resolve().parent
AUTO = ("start", "finish", "dispatch", "merge")


def script(name, *args, stdin=None):
    """Runs step script `name`; returns (its last line, its exit code)."""
    p = subprocess.run([sys.executable, "-I", str(SCRIPTS / f"{name}.py"), *map(str, args)], input=stdin,
                       capture_output=True, text=True)
    lines = [ln for ln in p.stdout.splitlines() if ln.strip()]
    return (lines[-1] if lines else f"FAIL {name} no-output: {p.stderr.strip()[-200:]}"), p.returncode


def launch(run, ident, remote, *extra):
    """Writes the prompt of task `ident` and returns its launch line, or the FAIL line."""
    line, rc = script("task_prompt", run, ident, *(["--remote"] if remote else []), *extra)
    if rc or not line.startswith("OK "):
        return line
    fields = line.split(" ", 3)
    if fields[2] == "remote":
        return f"remote {ident} {fields[3][fields[3].index('args='):]}  (build.md)"
    prompt = fields[3].split(" ")[0]
    title = taskwt.title(runfolder.task_file(run, ident))
    return f"agent {ident} {prompt} description=implement: {ident} {title}  (build.md)"


def go(run, remote, out):
    """Does every action needing no decision until nothing moves; appends what it ran and launched to `out`."""
    launched = False
    for _ in range(100):
        d = decisions.load(run)
        if not d or (d.get("approved") or "").strip() != "yes":
            return
        slots = int(d.get("parallel") or 1)
        lines = build_actions(run, d, slots)
        moved = False
        launches = []
        for line in lines:
            act, _, rest = line.partition(" ")
            ident = rest.split(" ")[0]
            if act == "start":
                res, rc = script("start_ticket", run, ident)
                out.append(res)
                moved |= rc == 0
            elif act == "finish":
                res, rc = script("finish_task", run, ident)
                out.append(res)
                moved |= rc == 0
            elif act == "ship" and rest.split(" ")[1:2] in (["open"], ["merge"]):
                res, rc = script("ship_ticket", run, ident, rest.split(" ")[1])
                out.append(res)
                moved |= rc == 0
            elif act == "dispatch":
                res, rc = script("start_task", run, ident)
                out.append(res)
                if rc:
                    continue
                conflict = re.search(r"conflict=(\S+)", res)
                if conflict:
                    launches.append(launch(run, ident, False, "--sync", conflict.group(1)))
                else:
                    launches.append(launch(run, ident, remote))
                moved = True
        out.extend(launches)
        launched |= bool(launches)
        if launches or moved:
            continue
        if launched:  # the agents start first: a merge would hold them for its Checks
            return
        for line in lines:  # merges run the Checks, minutes each: only once no agent waits to be started
            act, _, rest = line.partition(" ")
            if act == "merge":
                res, rc = script("merge_task", run, rest.split(" ")[0])
                out.append(res)
                moved |= rc == 0
        if not moved:
            return


def ran_remote(run, ident):
    """True when task `ident`'s latest prompt (`prompts/<ident>-<n>.md`) started a remote agent."""
    prompts = [(int(p.stem.rsplit("-", 1)[1]), p) for p in (run / "prompts").glob(f"{ident}-*.md")
               if p.stem.rsplit("-", 1)[1].isdigit()]
    if not prompts:
        return False
    return '"remote": true' in max(prompts)[1].read_text(encoding="utf-8")


def run_remote(run, flags):
    """Whether this call dispatches remotely: `--local` or `--remote` when given, else `decisions.md`'s `remote:`."""
    if "--local" in flags:
        return False
    if "--remote" in flags:
        return True
    d = decisions.load(run)
    return bool(d) and (d.get("remote") or "").strip() == "on"


def returned(run, ident, remote, title, message, out):
    """`--returned`: the checks and finish after a task's agent returned, then `go`; `remote` is where the task
    ran."""
    args = [run, ident] + (["--from-report"] if remote else [])
    res, rc = script("report_check", *args, stdin=None if remote else message)
    out.append(res)
    if rc:
        if "brief-violation" in res and not list((run / "prompts").glob(f"{ident}-*-output.json")):
            out.append(launch(run, ident, remote, "--reissue"))
        return
    if not remote:
        capped = Path.home() / ".agents" / "bin" / "capped"
        if capped.exists():  # an agent's capped commands can outlive it: stop them before committing its work
            subprocess.run([str(capped), "stop", str(run / "worktrees" / ident)], capture_output=True)
    res, rc = script("finish_task", run, ident, *(["--title", title] if title else []))
    out.append(res)
    if rc == 0:
        go(run, run_remote(run, []), out)


def reviewed(run, tt, path, base, note, remote, out):
    """`--reviewed`: posts a ticket review and renders it for the triage, then `go`."""
    text = runfolder.ticket_file(run, tt).read_text(encoding="utf-8")
    pr = ci.pr_number(runfolder.header(text, "PR"))
    if not pr:
        out.append(f"FAIL {tt} no-pr")
        return
    if not base:
        d = decisions.load(run)
        base = "origin/" + ((d.get("base") if d else None) or runfolder.run_info(run)["Default"])
    res, rc = script("post_review", run, pr, path, "--base", base, *(["--note", note] if note else []))
    out.append(res)
    if rc:
        return
    role = "security" if str(path).endswith("-security.json") else "reviewer"
    view = subprocess.run([sys.executable, "-I", str(SCRIPTS.parents[1] / "_shared" / "protocol" / "render.py"), role,
                           str(path)], capture_output=True, text=True)
    out.extend(line for line in view.stdout.splitlines())
    go(run, remote, out)


def main(argv):
    if not argv:
        print(__doc__.split("\n\n")[1])
        return 2
    run = Path(argv[0]).expanduser().resolve()
    if not run.is_dir():
        print(f"FAIL run no-run-folder: {run}")
        return 1
    if len(argv) == 4 and argv[1] == "--triaged" and argv[3] in ("fix", "none"):
        line, code = record_triage(run, argv[2], argv[3])
        print(line)
        return code
    flags = argv[1:]
    if flags and flags[0] in ("--go", "--returned", "--reviewed"):
        remote = run_remote(run, flags)
        title = flags[flags.index("--title") + 1] if "--title" in flags[:-1] else None
        out = []
        if flags[0] == "--returned" and len(flags) > 1 and not flags[1].startswith("--"):
            here = "--local" in flags or ("--remote" not in flags and not ran_remote(run, flags[1]))
            returned(run, flags[1], not here, title, sys.stdin.read() if here else None, out)
        elif flags[0] == "--reviewed" and len(flags) > 2:
            opt = {k: flags[flags.index(k) + 1] for k in ("--base", "--note") if k in flags[:-1]}
            reviewed(run, flags[1], flags[2], opt.get("--base"), opt.get("--note"), remote, out)
        elif flags[0] == "--go":
            go(run, remote, out)
        else:
            print("FAIL run usage: next.py <run> --returned <TT-NN> [--title <subject>]")
            return 2
        d = decisions.load(run)
        slots = int(d.get("parallel") or 1) if d else 1
        left = setup_action(run, d, slots) or build_actions(run, d, slots)
        launched = {ln.split(" ")[1] for ln in out if ln.startswith(("agent ", "remote "))}
        left = [ln for ln in left if not (ln.startswith("wait ") and ln.split(" ")[1] in launched)]
        print("\n".join(out + [how(line, run_remote(run, [])) for line in left]))
        return 0
    if len(flags) != 0:
        print("FAIL run usage: next.py <run> [--go | --returned <TT-NN> ... | --triaged <TT> fix|none]")
        return 2
    d = decisions.load(run)
    slots = int(d.get("parallel") or 1) if d else 1
    lines = setup_action(run, d, slots) or build_actions(run, d, slots)
    print("\n".join(how(line, run_remote(run, [])) for line in lines))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
