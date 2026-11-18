#!/usr/bin/env python3
"""Says what the orchestrator does next, read from the run folder alone: build-epic's state machine.

Usage: python3 -I next.py <run>
       python3 -I next.py <run> --go [--no-ship]
       python3 -I next.py <run> --returned <TT-NN> [--title <subject>] [--no-ship] [< message]
       python3 -I next.py <run> --reviewed <TT> <output> [--base <ref>] [--note <text>] [--no-ship]
       python3 -I next.py <run> --wait [--no-ship]
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
  ship <TT> post pr=<n> output=<file> [base=<sha>]   a review of the head, not yet posted: verify, then `--reviewed`
                                                  ship.md
  wait <id> [why]      a task in flight with no output yet (`remote`: a run `--go` started, `--wait` collects it),
                       or a ticket waiting on a human or another PR
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
(`ship_ticket.py`; `--no-ship` leaves those two as `ship` lines, for a run told to stop before shipping). It never
runs `ship merge` while `unguarded` finds clauses: the line is left for the orchestrator, naming them. Each
script's line is printed as it ran.
A prepared task prints `agent <TT-NN> prompt=<file> description=<text>` (start an `epic-implementer` with the file's
text) or, remotely, `remote <TT-NN> started log=<file>`: `--go` started `remote-agent` with the prompt's arguments
itself, detached, its stdout to `<run>/remote/<TT-NN>.out`, stderr to `.log` and exit code to `.exit` beside it, its
pid in `.pid` (without `~/.agents/bin/remote-agent`, `remote <TT-NN> args=<arguments>`: run it in the background,
its stdout to `<run>/reports/<TT-NN>.json`); then the actions left, as without `--go`. Every `--go`, `--returned`,
`--reviewed` and `--wait` first collects each detached run that ended: on exit 0 or 3 its output becomes
`reports/<TT-NN>.json`, checked and finished as `--returned` does; any other exit, or a wrapper gone with no exit code
(`lost`), prints `FAIL <TT-NN> remote-exit <code>: <its last stderr lines> log=<file>` and leaves the task to the
orchestrator. A script's `FAIL` stops that action and is
printed with its log; its step file says what to do.

`--returned <TT-NN>` is `--go` after a task's agent returned: `report_check.py` on the final message (stdin; for a
task whose latest prompt was a remote one, the saved output, `--from-report`, and stdin is not read), `capped stop` on its worktree when it ran here, `finish_task.py`
(`--title`, when given, is the commit subject), then `--go`. A first `brief-violation` writes the reissue prompt
(`task_prompt.py --reissue`) and prints it as an `agent` or `remote` line; a second prints the `FAIL` alone.

With remote on, `--go` also starts a `ship <TT> review` or `re-review` line's reviewers itself, detached, as
`start_review` says (`remote <TT> review|security started name=<name> log=<file>`); the ticket shows `wait <TT> review
remote` until they end, then `ship <TT> post ...` once `collect_review` copied, checked and rendered each output. A
review it cannot run remotely prints `FAIL <TT> remote-review <why>` or `FAIL <TT> remote-exit <code>: ...`, leaves a
`remote/review-<PR>-<sha7>.local` marker, and the `review` line comes back for a reviewer here.

`--wait` is `--go`, then, while every action left is a `wait` line and a detached run is in flight, waits for one to
end and does `--go` again, for up to `BUILD_EPIC_WAIT_SECS` seconds (default 540, under a 10-minute shell timeout);
it returns at the first `FAIL` or the first line that is not a `wait`.

`--reviewed <TT> <output>` is `--go` after a ticket's reviewer returned: `post_review.py` posts the output on the
ticket's PR (`--base`, default `origin/<the run's base>`; `--note` passes through) and prints its line, then the
output rendered (`render.py`, `security` for a `-security.json` output) for the triage, then `--go`.
"""
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import status  # noqa: E402
from steplib import ci, core, decisions, guard, identity, runfolder, taskwt  # noqa: E402

PLAN_CHECK = Path(__file__).resolve().parents[1] / "plan_check.py"
DONE_LIKE = ("merged", "in-review", "claimed")
REMOTE_AGENT = Path.home() / ".agents" / "bin" / "remote-agent"
REVIEW_PR = Path(__file__).resolve().parents[2] / "review-pr" / "scripts"
PROTOCOL = Path(__file__).resolve().parents[2] / "_shared" / "protocol"
WAIT_SECS = int(os.environ.get("BUILD_EPIC_WAIT_SECS") or 540)  # under a 10-minute shell timeout
POLL_SECS = 3


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
    red = re.search(r"^rc: *(\d+)", (run / "skips.md").read_text(encoding="utf-8"), re.MULTILINE) \
        if (run / "skips.md").is_file() else None
    if not get("test_command") or (red and red.group(1) != "0" and get("red_base") != "accept"):
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
    fresh = not posted or (tip and not tip.startswith(posted[:7]) and not posted.startswith(tip[:7]))
    if fresh and tip:
        stem = f"{pr}-{tip[:7]}"
        if any((run / "remote" / f"{who}-{stem}.pid").is_file() for who in ("review", "security")):
            return f"wait {ident} review remote  (ship.md)"
        out = run / "reviews" / f"{stem}.json"
        if out.is_file():
            return f"ship {ident} post pr={pr} output={out}{f' base={posted}' if posted else ''}  (ship.md)"
    if not posted:
        return f"ship {ident} review pr={pr}  (ship.md)"
    if fresh:
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
                elif (run / "remote" / f"{ident}.pid").is_file():
                    wait.append(f"wait {ident} remote  (build.md)")
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
    "wait": "a remote run `next.py <run> --go` started: `next.py <run> --wait` collects it once it ends",
    "post": "verify each finding of the output (and of its `-security.json` beside it, when there is one) against the "
            "code, then `next.py <run> --reviewed <id> <output> [--note <your verification>]` for each, with "
            "`--base <the line's base=>` only when the line has `base=` (a re-review); never the head reviewed",
    "open": "`next.py <run> --go` opens it", "ship-merge": "`next.py <run> --go` merges it",
    "ship-review": "a `pr-reviewer` on `review_prompt.py ticket --pr <n>` (plus a `security-reviewer` when `tier.py` "
                   "says high), then `next.py <run> --reviewed <id> <output>`",
    "ship-review-remote": "remote is on: `next.py <run> --go` starts its remote reviewers itself; after a "
                          "`FAIL <id> remote-review` or `remote-exit` line, a `pr-reviewer` here on "
                          "`review_prompt.py ticket --pr <n>` (plus a `security-reviewer` when `tier.py` says high), "
                          "then `next.py <run> --reviewed <id> <output>`",
    "triage": "Fix = a blocker, or a should-fix breaking an acceptance criterion or a written rule; none: "
              "`--triaged <id> none`; else a `<id>-NN-review-fixes` task and `--triaged <id> fix`",
    "re-review": "a `pr-reviewer` on `review_prompt.py re-review --base <head reviewed> --findings <findings file>`, "
                 "then `next.py <run> --reviewed <id> <output> --base <head reviewed>`",
    "re-review-remote": "remote is on: `next.py <run> --go` starts its remote reviewer itself; after a "
                        "`FAIL <id> remote-review` or `remote-exit` line, a `pr-reviewer` here on `review_prompt.py "
                        "re-review --base <head reviewed> --findings <findings file>`, then "
                        "`next.py <run> --reviewed <id> <output> --base <head reviewed>`",
    "clean": "`clean.py <run>`", "report": "`status.py <run> --write`, then show `view.py <run> report`",
}


def unguarded(run, d):
    """The Authorizations clauses `d` does not waive `yes`, when `origin/<default>` has no `agent-merge-guard` block
    to enforce them (`../merge-guard.md`); [] when it has one or every clause is waived. Without a block the guard
    passes every diff, so a `no` the human gave would go unenforced if `--go` merged the ticket PR itself."""
    open_ = [a.clause for a in (d.authorizations if d else []) if a.waived != "yes"]
    if not open_:
        return []
    repo = core.resolve(runfolder.run_info(run)["Repo"])
    (run / "logs").mkdir(exist_ok=True)
    with open(run / "logs" / "next-guard.log", "a", encoding="utf-8") as log:
        try:
            return [] if guard.load(repo, f"origin/{runfolder.run_info(run)['Default']}", log) else open_
        except core.Fail:
            return open_


def how(line, remote=False, held=()):
    """`line` with the one instruction its action needs inside its step-file note, so the step file is read only for
    a `FAIL` or a case the instruction does not cover; a ticket review's names the remote way when `remote`, and a
    ticket merge the clauses `held` (`unguarded`) to check by hand."""
    head, sep, note = line.partition("  (")
    if not sep:
        return line
    words = head.split(" ")
    key = words[0]
    if key == "ship" and len(words) > 2:
        key = {"review": "ship-review", "merge": "ship-merge"}.get(words[2], words[2])
    text = (remote and HOW.get(f"{key}-remote")) or HOW.get(key)
    if key == "ship-merge" and held:
        text = HELD.format(clauses="; ".join(f'"{c}"' for c in held))
    return f"{head}  ({note[:-1]}: {text})" if text else line


HELD = ("no `agent-merge-guard` block enforces the clauses this run did not waive ({clauses}): check the PR's files "
        "and diff (`gh pr view <n> --json files`, `gh pr diff <n>`) against each; on a hit, ask the human and merge "
        "only once they waive it (`decide.py <run> clause \"<clause>\" yes \"<their words>\"`); with no hit, "
        "`ship_ticket.py <run> <id> merge`, then `next.py <run> --go`")


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
    """Writes the prompt of task `ident` and returns its launch line, or the FAIL line. A remote one is started here,
    detached (`start_remote`), when `remote-agent` is installed."""
    line, rc = script("task_prompt", run, ident, *(["--remote"] if remote else []), *extra)
    if rc or not line.startswith("OK "):
        return line
    fields = line.split(" ", 3)
    if fields[2] == "remote":
        args = fields[3][fields[3].index("args=") + 5:]
        if REMOTE_AGENT.exists():
            return start_remote(run, ident, shlex.split(args))
        return f"remote {ident} args={args}  (build.md)"
    prompt = fields[3].split(" ")[0]
    title = taskwt.title(runfolder.task_file(run, ident))
    return f"agent {ident} {prompt} description=implement: {ident} {title}  (build.md)"


def remote_files(run, ident):
    """The files of task `ident`'s detached remote run under `<run>/remote/`: its pid, its exit code once it ended,
    its stdout (the agent's output) and its stderr (progress, the `transcript:` line among it)."""
    base = run / "remote" / ident
    return {k: base.with_name(f"{ident}.{k}") for k in ("pid", "exit", "out", "log")}


def start_remote(run, ident, args, cwd=None):
    """Starts `remote-agent <args>` detached from this call, in `cwd` (default: task `ident`'s worktree), its stdout
    to `remote/<id>.out`, stderr to `remote/<id>.log`, and its exit code to `remote/<id>.exit` once it ends; returns
    its `started` line."""
    f = remote_files(run, ident)
    f["pid"].parent.mkdir(parents=True, exist_ok=True)
    for k in ("exit", "out"):
        f[k].unlink(missing_ok=True)
    wrapper = '"$@" >"$OUT" 2>"$LOG"; echo $? >"$EXIT.tmp" && mv "$EXIT.tmp" "$EXIT"'
    env = dict(os.environ, OUT=str(f["out"]), LOG=str(f["log"]), EXIT=str(f["exit"]))
    p = subprocess.Popen(["/bin/sh", "-c", wrapper, "sh", str(REMOTE_AGENT), *args], cwd=str(cwd or run / "worktrees" / ident),
                         env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    f["pid"].write_text(f"{p.pid}\n", encoding="utf-8")
    return f"remote {ident} started log={f['log']}  (build.md)"


def in_flight(run):
    """The ids of detached remote runs not yet collected."""
    return sorted(p.stem for p in (run / "remote").glob("*.pid")) if (run / "remote").is_dir() else []


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def collect(run, out):
    """For each detached remote run that ended: its output moved to `reports/<id>.json` and checked, then the task
    finished (exit 0 or 3), or a `FAIL <id> remote-exit <code>: <its last stderr lines>` for the orchestrator to act on
    as remote-subagents step 4 says. Returns whether a task finished."""
    moved = False
    for ident in in_flight(run):
        f = remote_files(run, ident)
        if not ended(run, ident):
            continue
        if f["pid"].with_suffix(".meta").is_file():
            collect_review(run, ident, out)
            continue
        # no exit code: the wrapper itself is gone (a reboot, a kill), so none will come
        code = f["exit"].read_text(encoding="utf-8").strip() if f["exit"].is_file() else "lost"
        if code in ("0", "3") and f["out"].is_file():
            # the output moves before the run's files go: a run folder with no `reports/` yet (a replay) once crashed
            # here after the pid was gone, and the output was lost
            (run / "reports").mkdir(exist_ok=True)
            os.replace(f["out"], run / "reports" / f"{ident}.json")
        f["pid"].unlink()
        f["exit"].unlink(missing_ok=True)
        if (run / "reports" / f"{ident}.json").is_file() and code in ("0", "3"):
            moved |= check_finish(run, ident, True, None, None, out)
            continue
        tail = [ln for ln in (f["log"].read_text(encoding="utf-8", errors="replace").splitlines()
                              if f["log"].is_file() else []) if ln.strip()][-3:]
        out.append(f"FAIL {ident} remote-exit {code}: {' | '.join(tail) or 'no stderr'} log={f['log']}")
    return moved


def go(run, remote, out, ship=True):
    """Does every action needing no decision until nothing moves; appends what it ran and launched to `out`."""
    launched = False
    held = None
    for _ in range(100):
        d = decisions.load(run)
        if not d or (d.get("approved") or "").strip() != "yes":
            return
        slots = int(d.get("parallel") or 1)
        moved = collect(run, out)
        lines = build_actions(run, d, slots)
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
            elif ship and act == "ship" and rest.split(" ")[1:2] in (["open"], ["merge"]):
                if rest.split(" ")[1] == "merge":
                    held = unguarded(run, d) if held is None else held
                    if held:
                        continue
                res, rc = script("ship_ticket", run, ident, rest.split(" ")[1])
                out.append(res)
                moved |= rc == 0
            elif (act == "ship" and remote and REMOTE_AGENT.exists()
                  and rest.split(" ")[1:2] in (["review"], ["re-review"])):
                launches += start_review(run, ident, rest.split(" ")[1], re.search(r"pr=(\d+)", rest).group(1))
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


def check_finish(run, ident, remote, title, message, out):
    """`report_check.py` on a returned task's output, then `finish_task.py`; `remote` is where the task ran.
    Returns whether it finished."""
    args = [run, ident] + (["--from-report"] if remote else [])
    res, rc = script("report_check", *args, stdin=None if remote else message)
    out.append(res)
    if rc:
        if "brief-violation" in res and not list((run / "prompts").glob(f"{ident}-*-output.json")):
            out.append(launch(run, ident, remote, "--reissue"))
        return False
    if not remote:
        capped = Path.home() / ".agents" / "bin" / "capped"
        if capped.exists():  # an agent's capped commands can outlive it: stop them before committing its work
            subprocess.run([str(capped), "stop", str(run / "worktrees" / ident)], capture_output=True)
    res, rc = script("finish_task", run, ident, *(["--title", title] if title else []))
    out.append(res)
    return rc == 0


def returned(run, ident, remote, title, message, out, ship=True):
    """`--returned`: the checks and finish after a task's agent returned, then `go`; `remote` is where the task
    ran."""
    for k in ("pid", "exit", "out"):  # a detached run the orchestrator collected itself
        remote_files(run, ident)[k].unlink(missing_ok=True)
    if check_finish(run, ident, remote, title, message, out):
        go(run, run_remote(run, []), out, ship)


def wait(run, remote, out, ship=True, timeout=WAIT_SECS):
    """`--wait`: `go`, then, while every action left is a `wait` and a detached remote run is in flight, polls until
    one ends and does `go` again (which collects it), for up to `timeout` seconds."""
    deadline = time.monotonic() + timeout
    while True:
        go(run, remote, out, ship)
        d = decisions.load(run)
        slots = int(d.get("parallel") or 1) if d else 1
        left = setup_action(run, d, slots) or build_actions(run, d, slots)
        if any(ln.startswith("FAIL ") for ln in out) or not all(ln.startswith("wait ") for ln in left):
            return
        flying = in_flight(run)
        if not flying or time.monotonic() >= deadline:
            return
        while time.monotonic() < deadline and not any(ended(run, i) for i in flying):
            time.sleep(POLL_SECS)


def start_review(run, tt, kind, pr):
    """Prepares and starts, detached, the remote reviewers of ticket `tt`'s PR `pr` (`kind` `review` or
    `re-review`) as review-pr step 3 **Remotely** says: a worktree at the PR's head, `review_prompt.py --remote`, and
    `remote-agent` for the `pr-reviewer` (`review-<pr>-<sha7>`), plus the `security-reviewer` (`security-<pr>-<sha7>`)
    on a ticket review `tier.py` calls high. Returns its lines; on a refusal, a `FAIL` line and a `.local` marker so the
    orchestrator runs that review here."""
    path = runfolder.ticket_file(run, tt)
    text = path.read_text(encoding="utf-8")
    info = runfolder.run_info(run)
    repo = core.resolve(info["Repo"])
    d = decisions.load(run)
    base = (d.get("base") if d else None) or info["Default"]
    branch = runfolder.header(text, "Branch")
    (run / "logs").mkdir(exist_ok=True)
    log = open(run / "logs" / f"{tt}-remote-review.log", "a", encoding="utf-8")
    with log:
        head = git(repo, "rev-parse", branch)
        stem = f"{pr}-{head[:7]}"
        marker = run / "remote" / f"review-{stem}.local"

        def refuse(why):
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(why + "\n", encoding="utf-8")
            return [f"FAIL {tt} remote-review {why}: run this review here (ship.md)"]

        if marker.is_file():
            return []
        try:
            posted = json.loads((run / "logs" / f"PR{pr}-review.json").read_text(encoding="utf-8")).get("commit_id")
        except (OSError, ValueError, AttributeError):
            posted = None
        findings = run / "reviews" / f"{pr}-{(posted or '')[:7]}-findings.md"
        if kind == "re-review" and not (posted and findings.is_file()):
            return refuse(f"no-findings {findings.name}")
        allowed = [a for a in (identity.human_login(repo, log), runfolder.header(text, "Opened by")) if a]
        wt = run / "worktrees" / f"review-{stem}"
        if not wt.is_dir() and git(repo, "worktree", "add", "-q", "--detach", str(wt), head) is None:
            return refuse("worktree")
        roles = ["review"]
        if kind == "review":
            since = git(wt, "merge-base", f"origin/{base}", "HEAD")
            tier = subprocess.run([sys.executable, "-I", str(REVIEW_PR / "tier.py"), "--git", str(wt), since or ""],
                                  capture_output=True, text=True)
            if tier.stdout.split("\n", 1)[0].strip() == "high":
                roles.append("security")
        lines = []
        for role in roles:
            mode = "security" if role == "security" else ("re-review" if kind == "re-review" else "ticket")
            name = f"{role}-{stem}"
            folder = run / "reviews" / f"{stem}{'-security' if role == 'security' else ''}-remote"
            argv = [sys.executable, "-I", str(REVIEW_PR / "review_prompt.py"), mode, "--pr", pr, "--worktree", str(wt),
                    "--head", head, "--base", posted if kind == "re-review" else f"origin/{base}", "--run", str(run),
                    "--task", str(path), *(["--findings", str(findings)] if kind == "re-review" else []),
                    "--remote", str(folder)]
            for login in allowed:
                argv += ["--allow-author", login]
            p = subprocess.run(argv, capture_output=True, text=True, cwd=str(repo))
            log.write(f"$ {shlex.join(argv)}\n{p.stderr}")
            if p.returncode:
                reason = (p.stderr.strip().splitlines() or ["review_prompt"])[-1]
                return lines + refuse(f"{name} {reason}")
            prompt = run / "prompts" / f"{name}.md"
            prompt.parent.mkdir(exist_ok=True)
            prompt.write_text(p.stdout, encoding="utf-8")
            args = ["--name", name, "--agent", (folder / "agent.txt").read_text(encoding="utf-8").strip()]
            for line in (folder / "attach.txt").read_text(encoding="utf-8").splitlines():
                if line.strip():
                    args += ["--attach", line.strip()]
            meta = {"ticket": tt, "role": "security" if role == "security" else "reviewer", "worktree": str(wt),
                    "output": str(run / "reviews" / f"{stem}{'-security' if role == 'security' else ''}.json")}
            (run / "remote").mkdir(exist_ok=True)
            (run / "remote" / f"{name}.meta").write_text(json.dumps(meta), encoding="utf-8")
            lines.append(start_remote(run, name, args + [f"@{prompt}", str(wt)], cwd=repo).replace(
                f"remote {name} started", f"remote {tt} {role} started name={name}"))
        return lines


def collect_review(run, name, out):
    """A detached remote reviewer `name` that ended: `remote-agent result` saved to `remote/<name>.json` (its cost),
    its report copied to the review's output file and checked, rendered for the triage; its worktree removed once no
    reviewer of that head is left. Any failure prints a `FAIL` line and a `.local` marker, so the review runs here."""
    f = remote_files(run, name)
    meta = json.loads(f["pid"].with_suffix(".meta").read_text(encoding="utf-8"))
    code = f["exit"].read_text(encoding="utf-8").strip() if f["exit"].is_file() else "lost"
    for k in ("pid", "exit", "out"):
        f[k].unlink(missing_ok=True)
    f["pid"].with_suffix(".meta").unlink()
    repo = core.resolve(runfolder.run_info(run)["Repo"])
    res = subprocess.run([str(REMOTE_AGENT), "result", name], capture_output=True, text=True, cwd=str(repo))
    report = None
    if res.returncode == 0:
        (run / "remote" / f"{name}.json").write_text(res.stdout, encoding="utf-8")
        try:
            report = Path(json.loads(res.stdout).get("report_path") or "")
        except (ValueError, AttributeError):
            report = None
    stem = name.split("-", 1)[1]
    ok = code in ("0", "3") and report and report.is_file() and report.read_text(encoding="utf-8").strip()
    if ok:
        Path(meta["output"]).parent.mkdir(exist_ok=True)
        shutil.copyfile(report, meta["output"])
        check = subprocess.run([sys.executable, "-I", str(PROTOCOL / "check.py"), meta["role"], "output",
                                meta["output"]], capture_output=True, text=True)
        line = (check.stdout.strip().splitlines() or ["FAIL check no-output"])[-1]
        out.append(line)
        if line.startswith("OK "):
            view = subprocess.run([sys.executable, "-I", str(PROTOCOL / "render.py"), meta["role"], meta["output"]],
                                  capture_output=True, text=True)
            out.extend(view.stdout.splitlines())
    else:
        tail = [ln for ln in (f["log"].read_text(encoding="utf-8", errors="replace").splitlines()
                              if f["log"].is_file() else []) if ln.strip()][-3:]
        out.append(f"FAIL {meta['ticket']} remote-exit {code}: {' | '.join(tail) or 'no report'} log={f['log']}"
                   " (ship.md: run this review here)")
        Path(meta["output"]).unlink(missing_ok=True)
        (run / "remote" / f"review-{stem}.local").write_text(f"{name} exit {code}\n", encoding="utf-8")
    if not any((run / "remote" / f"{who}-{stem}.pid").is_file() for who in ("review", "security")):
        git(repo, "worktree", "remove", "--force", meta["worktree"])


def ended(run, ident):
    """Whether task `ident`'s detached run ended: its exit code written, or its wrapper process gone."""
    f = remote_files(run, ident)
    if f["exit"].is_file():
        return True
    try:
        return not alive(int(f["pid"].read_text(encoding="utf-8").strip()))
    except (OSError, ValueError):
        return True


def reviewed(run, tt, path, base, note, remote, out, ship=True):
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
    go(run, remote, out, ship)


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
    if flags and flags[0] in ("--go", "--returned", "--reviewed", "--wait"):
        remote = run_remote(run, flags)
        ship = "--no-ship" not in flags
        title = flags[flags.index("--title") + 1] if "--title" in flags[:-1] else None
        out = []
        if flags[0] == "--returned" and len(flags) > 1 and not flags[1].startswith("--"):
            here = "--local" in flags or ("--remote" not in flags and not ran_remote(run, flags[1]))
            returned(run, flags[1], not here, title, sys.stdin.read() if here else None, out, ship)
        elif flags[0] == "--reviewed" and len(flags) > 2:
            opt = {k: flags[flags.index(k) + 1] for k in ("--base", "--note") if k in flags[:-1]}
            reviewed(run, flags[1], flags[2], opt.get("--base"), opt.get("--note"), remote, out, ship)
        elif flags[0] == "--go":
            go(run, remote, out, ship)
        elif flags[0] == "--wait":
            wait(run, remote, out, ship)
        else:
            print("FAIL run usage: next.py <run> --returned <TT-NN> [--title <subject>] | --wait")
            return 2
        d = decisions.load(run)
        slots = int(d.get("parallel") or 1) if d else 1
        left = setup_action(run, d, slots) or build_actions(run, d, slots)
        launched = {ln.split(" ")[1] for ln in out if ln.startswith(("agent ", "remote "))}
        left = [ln for ln in left if not (ln.startswith("wait ") and ln.split(" ")[1] in launched)]
        held = unguarded(run, d) if any(ln.startswith("ship ") for ln in left) else []
        print("\n".join(out + [how(line, run_remote(run, []), held) for line in left]))
        return 0
    if len(flags) != 0:
        print("FAIL run usage: next.py <run> [--go | --wait | --returned <TT-NN> ... | --triaged <TT> fix|none]")
        return 2
    d = decisions.load(run)
    slots = int(d.get("parallel") or 1) if d else 1
    lines = setup_action(run, d, slots) or build_actions(run, d, slots)
    held = unguarded(run, d) if any(ln.startswith("ship ") for ln in lines) else []
    print("\n".join(how(line, run_remote(run, []), held) for line in lines))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
