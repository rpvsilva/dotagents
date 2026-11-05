---
name: build-epic
description: Take an epic (or one issue) from the tracker to one reviewed PR per ticket, each built from task PRs by planner, explorer and implementer subagents, with one human approval of the plan.
disable-model-invocation: true
---

Build an epic as **tickets** made of **tasks**. A ticket is one tracker issue: its own branch `feat/<ticket-slug>` and one PR into the default branch, so several developers can split an epic by ticket. A task is one implementer's slice of a ticket: its own branch and one PR into the ticket branch, so every change leaves a trail on the tracker. A ticket whose suites are **slow** gets one **tuning task** after its last implementer task: a test-tuner's own branch and PR into the ticket branch, making the tests the ticket touched faster and better covered.

Work runs in **lanes** ([planner-brief.md](planner-brief.md) pass B): tickets that share files run one after another, and only lanes with disjoint write sets run in parallel. A run that fills fewer slots and merges cleanly beats one that fills them all and spends its runs on conflicts.

You are the **orchestrator**: you start subagents, track statuses, open and merge PRs, and talk to the human. Keep your context **thin**: you read the run folder's short files (ticket and task headers, the `Checks` section of `map.md`, reports), not the code or the tracker; the one exception is verifying review findings in step 7. Every heavy read happens in a subagent that writes its result to a file and returns a short report.

Input: an epic or issue (link or ID, on any tracker), or plain text. Optional: `--parallel N`; `--explore` to always run the explorers.

## Run folder

`~/.agents/runs/<repo>/<epic-slug>/`: `<repo>` is the repo folder's name, `<epic-slug>` the tracker ID plus a few words (`gh-12-due-dates`). It holds:

- `spec.md`, `areas.md`, `notes/<area>.md`, `map.md`: written by the planner and explorers.
- `tickets/TT-<slug>.md`: one per ticket. Its `Status:` line is yours: `ready`, `claimed` (another developer builds it), `building`, `in-review` (its PR waits on a human), `merged`, `blocked`. Add a `PR:` line when you open its draft PR in step 6, or in step 7 when no draft was opened (the host has no PR checks).
- `tasks/TT-NN-<slug>.md`: one per task, `TT` its ticket. Its `Status:` line is yours: `ready`, `in-progress`, `done` (its PR is merged), `blocked`. Add a `PR:` line when you open its PR.
- `tasks/TT-tests.md`: the ticket's tuning task, when you write one (step 6): a title, `Status:`, and `Range: <ticket branch's first commit>..<tip>`, the commits its tasks added.
- `followups.md`: work found but left out of the build (review findings outside a ticket's acceptance criteria), one line each with its ticket.
- `worktrees/TT-NN/` and `worktrees/TT-tests/` (one per task being built) and `worktrees/TT/` (one per ticket being shipped).
- `dispatcher.sha` and `worktrees/dispatcher/`: the pinned dispatcher, only when the epic changes the dispatcher's own repo (see [Remote dispatcher](#remote-dispatcher)).

Subagents leave every `Status:` and `PR:` line alone.

When the folder already exists, **resume**: set each `in-review` ticket whose PR has merged to `merged`; set every `in-progress` task with no PR back to `ready` and remove its worktree and branch; finish every task whose PR has merged but is not yet `done` (set it `done`, remove its worktree and branch, do **Ticket CI**); and take every task with an open PR to **Merge** in step 6. When `dispatcher.sha` exists, reuse that SHA: re-create `worktrees/dispatcher/` at it if the worktree is gone, never at a newer commit. Then continue at the first step whose done-condition is not met.

## Subagents

| Agent | Brief | Writes |
|---|---|---|
| `epic-planner` | [planner-brief.md](planner-brief.md) | `spec.md`, `areas.md`, `map.md`, `tickets/`, `tasks/` |
| `epic-explorer` | [explorer-brief.md](explorer-brief.md) | `notes/<area>.md` |
| `epic-implementer` | [implementer-brief.md](implementer-brief.md) | commits on its task branch |
| `test-tuner` | [tuner-brief.md](../tune-tests/tuner-brief.md) | commits on its tuning task's branch |

Start and limit them as in [Subagents](../_shared/conventions.md#subagents): at most **N** at once (the **slots**).

The `epic-implementer` and `test-tuner` are **remote-capable** ([Remote subagents](../_shared/conventions.md#remote-subagents)): name each after its task's file, `--name TT-NN-<slug>`, and attach its task, `spec.md`, `map.md` and `notes/`. Dispatch it with the command [Remote dispatcher](#remote-dispatcher) names. When it returns, commit its changes on the task branch and do its step 5 for it: `git fetch origin && git merge <ticket-branch>` in its worktree. On a conflict, send it back to resolve the conflict markers, then commit the merge. Act on its report after that. The planner and explorers write into the run folder, so they always run here.

Every subagent ends with this report, and you act on it alone:

```
status: done | blocked | plan-wrong
changed: <paths, or the last commit SHA>
why: <one line, when status is not done>
follow-up: <optional, implementers only: work a later task must pick up>
```

The `test-tuner` reports `done` or `blocked`, with its `time`, `covered`, `moved` and `left` lines on top; they go in its PR's body.

### Remote dispatcher

The remote dispatcher is `~/.agents/bin/remote-agent`, a link into the checkout that installed it. When the epic changes that same repo, merges during the epic would change the dispatcher under the running tasks, so dispatch from a pinned copy instead:

1. In step 1, compare the real path of `~/.agents/bin/remote-agent` (`realpath`, or `readlink -f`) with the real path of `scripts/remote-agent.sh` in the epic's repo. When they differ, or the link is missing, dispatch with `~/.agents/bin/remote-agent` and skip the rest of this section.
2. When they match, once per run: `git fetch origin`, write the commit of `origin/<default-branch>` into `<run>/dispatcher.sha`, and `git worktree add --detach <run>/worktrees/dispatcher <sha>`. On resume, read the SHA from `dispatcher.sha` instead of fetching a new one.
3. Dispatch every remote run with `<run>/worktrees/dispatcher/scripts/remote-agent.sh` in place of `~/.agents/bin/remote-agent`, with the same arguments. It finds its sibling scripts from its own path, so it runs the pinned code. The pinned copy may predate features the epic adds; rely only on what it already does.

The pin covers the scripts only. The pinned `remote-agent.sh` still packs the sandbox's setup from `~/.agents` and `~/.claude` on this machine, following their links into the installed checkout, so remote runs get that checkout's working tree: neither the pinned commit nor the epic's branches. An epic that changes hooks, skills or briefs is therefore built under the installed versions; its changes reach remote runs only once the installed checkout holds them. Keep that checkout where it is during the epic (no `git pull`, no switching branches), so every task runs under the same setup.

## Tracker

Keep the tracker in step with the build, as the identity from step 1. Each ticket's `Issue` line names its issues, and every update below applies to each of them; with plain-text input there is none, and you skip this. The active login can change mid-run (another session switching accounts): confirm it is still that identity (`gh api user` on GitHub) before each tracker update, and when it is not, keep building, hold the updates, and ask the human; apply the held updates once the identity is back.

- **Claimed**: an issue assigned to anyone but the human you work for belongs to another developer. Claim each ticket you start by assigning its issue to the human (their own login, e.g. `gh api user --jq .login` run without the identity's token).
- **Planned**, once the human approves the plan (step 5) and again after each approved re-plan: put the plan on every ticket's issue, claimed ones included, so whoever builds it and whoever reviews its PR see what was decided. One comment per issue, starting with the marker `<!-- build-epic-plan -->`, holding: the answers decided for `spec.md`'s open questions that bear on this ticket; any change to the issue's scope or acceptance criteria; and the ticket's tasks (number, title, blocked by). Leave `map.md` out: it is for implementers. When the issue already holds a comment with the marker, edit that comment (on GitHub, find its `id` with `gh api repos/<owner>/<repo>/issues/<n>/comments` and `gh api -X PATCH repos/<owner>/<repo>/issues/comments/<id> -f body=...`), so each issue carries one current plan.
- **Started**, when a ticket starts: on GitHub, `gh label create "in progress" --force` once per run, then `gh issue edit <n> --add-label "in progress" --add-assignee <human>`. On other trackers, assign it and move it to its in-progress state through the connector. The `in_progress` Setting (e.g. a GitHub Projects status field) replaces this default.
- **Blocked**, when a task ends `blocked` or `plan-wrong`: add a `blocked` label (created the same way) to its ticket's issue and one comment naming the task and its `why`. Remove the label once a re-plan or fix lets the task run again.
- **Done** belongs to the ticket PR's merge. Into the default branch, its closing reference closes the issue. Into any other base (the human retargeted the epic to a feature branch), the host leaves the issue open: close it yourself after the merge, with a comment linking the PR (`gh issue close <n> --reason completed --comment <PR URL>` on GitHub). Once every issue of the epic is closed, close the epic too.

## Steps

1. **Set up.** [Learn the rules](../_shared/conventions.md#learn-the-rules) with notes file `.agents/build-epic-notes.md` (test commands, areas to avoid) and the `tracker`, `max_parallel`, `in_progress` and `tune_min_seconds` Settings, and the [ship-pr](../ship-pr/SKILL.md) rules from its step 1: commit convention and merge rule. Create the run folder, or resume it, and pin the [remote dispatcher](#remote-dispatcher) when subagents run remotely. Done when you know N, the identity, the human's login, the merge rule, the dispatch command, and the run folder exists.

2. **Plan, pass A.** Start an `epic-planner` with: pass `A`, the input, the run folder, the notes file path, and `explore: always` when `--explore` was given. Done when it reports `done` and `spec.md` and `areas.md` exist. On `blocked` (for example, no connector for the tracker), relay its `why` to the human and stop.

3. **Explore.** Skip this step when `areas.md` says `no exploration needed`. Otherwise start one `epic-explorer` per area, filling free slots: the area's name, question and starting paths, the `spec.md` path, and `notes/<area>.md` as its output. Done when every area has a note.

4. **Plan, pass B.** Start a fresh `epic-planner` with: pass `B`, the run folder, the notes file path. Done when `map.md`, `tickets/` and `tasks/` exist, every ticket and task is `ready`, and `python3 ~/.agents/skills/build-epic/plan_check.py <run>` prints `plan ok`; when it prints problems, start an `epic-planner` with pass `revise` and its output as the reason.

5. **Approve.** Set each ticket whose issue is **claimed** by someone else to `claimed`. Show the human: each ticket (number, issues, title, blocked by, and who claimed it) with its tasks under it (number, title, blocked by, check); `map.md`'s `Lanes`, as the blocker graph of tickets in a short indented tree, one branch per lane; the **approval cost**: how many ticket PRs the merge rule from step 1 leaves to a human (e.g. ones touching an approval-tier path), and which tickets wait on those merges before they can start; the **gaps** (tasks whose check is not an automated test, plus `map.md`'s `Gaps` section); each of `spec.md`'s `Open questions` with the answer the planner assumed; and the paths to `spec.md` and `map.md`. Wait for the answer. Set each ticket the human takes to `ready` and each one they leave to someone else to `claimed`. On a rejection, start an `epic-planner` with pass `revise` and the human's words verbatim, then show the plan again. Once approved, mark every ticket's issue **planned** (see [Tracker](#tracker)). Done when the human approves and every ticket with an issue is marked planned.

6. **Build.** Repeat until done:
   - **Start** each `ready` ticket whose `Blocked by` tickets are all `merged`: `git fetch origin && git push origin origin/<default-branch>:refs/heads/<ticket-branch>` (the ticket's `Branch` line), set it to `building`, and mark its issue **started** (see [Tracker](#tracker)). A ticket blocked by a `claimed` or `in-review` one waits for that PR to merge.
   - **Dispatch** each `ready` task of a `building` ticket whose task blockers are all `done`, whose `ticket NN` blockers are all `merged`, and whose `Writes` share no path with any `in-progress` task's, while a slot is free: `git fetch origin && git worktree add <run>/worktrees/TT-NN -b task/<ticket-slug>/NN origin/<ticket-branch>`; for a task with a `ticket NN` blocker (a hub task), also `git merge origin/<default-branch>` there, so it edits the hub files on top of the tickets merged before it. Set the task to `in-progress`, and start an `epic-implementer` with the task path, the run folder, the worktree path and `origin/<ticket-branch>`. A task you wrote yourself (tuning, fix) has no `Writes` line and runs alone in its ticket. A tuning task dispatches the same way, on `worktrees/TT-tests` and branch `task/<ticket-slug>/tests`, starting a `test-tuner` with the worktree path, its `Range` as the scope and the notes file path; it measures its own baseline.
   - **On `done`**: compare the files the task changed (`git diff --name-only origin/<ticket-branch>...HEAD` in its worktree) with its `Writes`, and append each path outside it to its `Writes` line, so the next Dispatch sees the real write set. Push the task branch and open its PR into the ticket branch, as the identity: the task's title in the commit convention, and a body with what it builds, its check and result, and a reference to the ticket's issue that leaves it open (`Part of #n` on GitHub). Record the PR on the task, then **Merge** it. For a tuning task, first **verify** it as [tune-tests](../tune-tests/SKILL.md) step 5 does, against the baseline in its report, sending what does not hold back to its tuner once; title its PR `test: tune <the ticket's title>`. A tuner that changed nothing ends the task `done` with no PR.
   - **Merge** task PRs one at a time per ticket. In the task's worktree, `git fetch origin`; when the branch lacks the ticket branch's tip, `git merge origin/<ticket-branch>`. Run every command in `map.md`'s `Checks` there, timing each suite: CI runs only on PRs into the default branch, so this run is the task PR's check, and its proof until the ticket's one review in step 7. When the ticket has a `PR:` line, read its checks first, as **Ticket CI** says. Push, then merge by the merge rule for PRs into a non-default branch; when the rule names none, `gh pr merge <n> --squash --delete-branch --match-head-commit <sha>`. On a conflict or a **red** check, send the task back to its implementer with the failure (continue the same subagent if your harness can, otherwise start a new one on the same worktree), then merge again. When merged, set the task to `done` and remove its worktree and branch, then do **Ticket CI**. When it was the ticket's last implementer task and any suite in this merge's checks ran for at least `tune_min_seconds`, write the ticket's tuning task `tasks/TT-tests.md`, `ready`, its `Range` the ticket branch's first commit to its tip. Add each `follow-up` as an acceptance criterion to the not-yet-started task of the same ticket it concerns, or, when none fits, keep it for that ticket's review-fixes task in step 7.
   - **Ticket CI**, so CI runs before the ticket ships (it runs only on PRs into the default branch). After **every** task merge, when the ticket has no `PR:` line yet (a first merge, or a run that stopped before opening it): `git fetch origin`, push the ticket branch if the merge left it only local, and open a **draft** PR from the ticket branch into the default branch as the identity (`gh pr create --draft --base <default-branch> --head <ticket-branch>` on GitHub). Title it by the commit convention (e.g. `feat(skills): <ticket title>`): the merge rule may squash-merge, making that title the commit on the default branch. In the body, reference its issue so it stays open (`Part of #n`). Record it as the ticket's `PR:` line. Where the host has no draft PRs, open it with a `WIP:` title prefix and drop the prefix in step 7. Where it has no PR checks, or the ticket PR still lists none a few minutes after its push (CI is off), skip Ticket CI for the rest of the run, tell the human once, and let the Merge step's local `Checks` stand as the ticket's CI. **Before** each later task merge into that ticket, read the ticket PR's checks: its head is still the previous merge's, so the result is that head's. Wait for pending ones to finish (`gh pr checks <n> --watch` on GitHub), since your merge would cancel them; other tickets keep building meanwhile. Ignore cancelled runs (a newer push replaced them). On a **red** check, read the failure (`gh run view <run-id> --log-failed`) and send it back as a failure: add it as an acceptance criterion to the ticket's next not-yet-started task; when none is left, write a fix task `tasks/TT-NN-fix-ci.md` with the failure as its check and dispatch it.
   - **On `blocked`**: set the task to `blocked`, mark its ticket's issue as **blocked** with the `why`, and keep dispatching the rest.
   - **On `plan-wrong`**: mark its ticket's issue as **blocked** with the `why`, stop dispatching and let running implementers finish. Start an `epic-planner` with pass `revise`, the task path and the implementer's `why`. Show the human its changed tickets and tasks as in step 5 and wait for approval, mark each changed ticket's issue **planned**, then resume dispatching.
   - **Ship** each `building` ticket whose tasks are all `done`, by step 7.

   Done when every ticket you build is `merged` or `in-review`. When the only ones left are `blocked`, or wait on a `blocked`, `claimed` or `in-review` ticket, report to the human and stop: each ticket branch keeps every merged task, and a later run resumes from there.

7. **Ship a ticket.** `git worktree add <run>/worktrees/TT <ticket-branch>` and follow [ship-pr](../ship-pr/SKILL.md) in it, from its "Open the PR" step through "Get it green", with the default branch as the base. The ticket's draft PR from step 6 is that PR: ship-pr updates its title and body and marks it ready instead of opening a new one. When step 6 opened none (the host has no PR checks), ship-pr opens it: record it as the ticket's `PR:` line. In the PR body, add a closing reference (`Closes #n` on GitHub) for the ticket's issue, and list its tasks (title, PR link, one line each). Then follow [review-pr](../review-pr/SKILL.md) on the PR: the ticket's one review. Sort its kept findings against the ticket's acceptance criteria (its tasks' `Acceptance criteria`) and the merge rule:
   - **Fix**: a `blocker`, or a `should-fix` that breaks an acceptance criterion or a written rule of the repo.
   - **Follow up**: everything else, hardening beyond the criteria included. Add each to `followups.md`, one line with its ticket and the review comment's link.

   When any finding is **Fix**, or step 6 kept an implementer `follow-up` for this ticket, write them as one new task `tasks/TT-NN-review-fixes.md` (each finding or follow-up an acceptance criterion quoted from its source, its scenario the check), run it through step 6, `git pull` in the ticket's worktree and get the PR green again. That task is the ticket's one fix round, and its checks are its proof. With nothing to fix, go straight on. Then follow ship-pr's "Merge" step: set the ticket to `merged` when the PR merged, or `in-review` when it waits on a human, and remove the ticket's worktree. Done when ship-pr's report is in hand.

8. **Clean up.** Remove any worktrees and task branches left, `worktrees/dispatcher/` included. After a merge, remove the `in progress` and `blocked` labels from the issues it closed. Keep the run folder.

## Report

One line per ticket: its PR URL and state (**merged**, **waiting** and on whom, or not started and why), then one line per task with its final status. Every `blocked` task with its `why`, every `claimed` ticket with who claimed it, the count of lines in `followups.md`, and the run folder's path.
