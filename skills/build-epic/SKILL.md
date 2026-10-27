---
name: build-epic
description: Take an epic (or one issue) from the tracker to one reviewed PR, with planner, explorer and implementer subagents and one human approval of the plan.
disable-model-invocation: true
---

Build a whole epic as one PR. You are the **orchestrator**: you start subagents, track ticket status and talk to the human. Keep your context **thin**: you read the run folder's short files (ticket headers, the `Checks` section of `map.md`, reports), not the code or the tracker; the one exception is verifying review findings in step 7. Every heavy read happens in a subagent that writes its result to a file and returns a short report.

Input: an epic or issue (link or ID, on any tracker), or plain text. Optional: `--parallel N`; `--explore` to always run the explorers.

## Run folder

`~/.agents/runs/<repo>/<epic-slug>/`: `<repo>` is the repo folder's name, `<epic-slug>` the tracker ID plus a few words (`gh-12-due-dates`). It holds:

- `spec.md`, `areas.md`, `notes/<area>.md`, `map.md`: written by the planner and explorers.
- `tickets/NN-<slug>.md`: one per ticket. Its `Status:` line (`ready`, `in-progress`, `done`, `blocked`) is yours to edit; subagents leave it alone.
- `worktrees/NN/`: one git worktree per ticket being built.

When the folder already exists, **resume**: set every `in-progress` ticket back to `ready`, remove its worktree and branch, and continue at the first step whose done-condition is not met.

## Subagents

| Agent | Brief | Writes |
|---|---|---|
| `epic-planner` | [planner-brief.md](planner-brief.md) | `spec.md`, `areas.md`, `map.md`, `tickets/` |
| `epic-explorer` | [explorer-brief.md](explorer-brief.md) | `notes/<area>.md` |
| `epic-implementer` | [implementer-brief.md](implementer-brief.md) | commits on its ticket branch |

Start the named agent if your harness defines it, otherwise a general-purpose subagent told to follow the brief. Hand over the inputs the brief lists, as **paths**, never summaries. If your harness has no subagents, follow each brief yourself, one at a time, writing the same files.

At most **N** subagents run at once (the **slots**): `--parallel N`, else `max_parallel` from the notes file, else 4.

Every subagent ends with this report, and you act on it alone:

```
status: done | blocked | plan-wrong
changed: <paths, or the last commit SHA>
why: <one line, when status is not done>
follow-up: <optional, implementers only: work a later ticket must pick up>
```

## Tracker

Keep the tracker in step with the build, as the identity from step 1. Each ticket's `Issues` line names its issues; with plain-text input there are none, and you skip this.

- **Started**, the first time any ticket of an issue is dispatched. On GitHub: `gh label create "in progress" --force` once per run, then `gh issue edit <n> --add-label "in progress"`, plus `--add-assignee @me` when the identity is a user account. On other trackers, move the issue to its in-progress state through the connector. An `in_progress` entry in the notes file (e.g. a GitHub Projects status field) replaces this default.
- **Blocked**, when a ticket ends `blocked` or `plan-wrong`: add a `blocked` label (created the same way) and one comment naming the ticket and its `why`. Remove the label once a re-plan or fix lets the ticket run again.
- **Done** belongs to the merge: the PR's closing references close the epic and its issues (step 7). Leave issues open and `in progress` until then.

## Steps

1. **Set up.** Read `.agents/build-epic-notes.md` if it exists (tracker, test commands, areas to avoid, `max_parallel`, `in_progress`). Learn the **identity** for tracker and GitHub calls from the repo's rules (`AGENTS.md` or `CLAUDE.md`), e.g. a `GH_TOKEN` prefix for a bot account; with none named, use the current login; if the rules name a setup that is missing, stop and ask. Create the run folder, or resume it. Done when you know N, the identity, and the run folder exists.

2. **Plan, pass A.** Start an `epic-planner` with: pass `A`, the input, the run folder, the notes file path, and `explore: always` when `--explore` was given. Done when it reports `done` and `spec.md` and `areas.md` exist. On `blocked` (for example, no connector for the tracker), relay its `why` to the human and stop.

3. **Explore.** Skip this step when `areas.md` says `no exploration needed`. Otherwise start one `epic-explorer` per area, filling free slots: the area's name, question and starting paths, the `spec.md` path, and `notes/<area>.md` as its output. Done when every area has a note.

4. **Plan, pass B.** Start a fresh `epic-planner` with: pass `B`, the run folder, the notes file path. Done when `map.md` and `tickets/` exist and every ticket is `ready`.

5. **Approve.** Show the human: each ticket (number, title, blocked by, its check), the blocker graph as a short indented tree, the **gaps** (tickets whose check is not an automated test, plus `map.md`'s `Gaps` section), each of `spec.md`'s `Open questions` with the answer the planner assumed, and the paths to `spec.md` and `map.md`. Wait for the answer. On a rejection, start an `epic-planner` with pass `revise` and the human's words verbatim, then show the plan again. Done when the human approves.

6. **Build.** The working tree must be clean: if it is not, ask the human. Create `feat/<epic-slug>` from the default branch and switch to it. Then repeat until done:
   - **Dispatch** each `ready` ticket whose blockers are all `done`, while a slot is free: `git worktree add <run>/worktrees/NN -b ticket/<epic-slug>/NN feat/<epic-slug>`, set the ticket to `in-progress`, mark its `Issues` as **started** (see [Tracker](#tracker)), and start an `epic-implementer` with the ticket path, the run folder, the worktree path and the epic branch name.
   - **On `done`**: `git merge --squash ticket/<epic-slug>/NN`, commit with the ticket's title in the repo's commit convention, run the commands in `map.md`'s `Checks` section. When the merge conflicts, undo it with `git reset --hard`; when a check goes **red**, drop the commit with `git reset --hard HEAD~1`. Either way, send the ticket back to its implementer with the failure (continue the same subagent if your harness can, otherwise start a new one on the same worktree). When green, set the ticket to `done` and remove its worktree and branch. Add each `follow-up` as an acceptance criterion to the not-yet-started ticket it concerns, or, when none fits, keep it for step 7's review-fixes ticket.
   - **On `blocked`**: set the ticket to `blocked`, mark its `Issues` as **blocked** with the `why`, and keep dispatching the rest.
   - **On `plan-wrong`**: mark the ticket's `Issues` as **blocked** with the `why`, stop dispatching and let running implementers finish. Start an `epic-planner` with pass `revise`, the ticket path and the implementer's `why`. Show the human its changed tickets as in step 5 and wait for approval, then resume dispatching.

   Done when every ticket is `done`. When the only tickets left are `blocked` or wait on a blocked one, report to the human and stop: the epic branch keeps every merged ticket.

7. **Ship.** Follow [ship-pr](../ship-pr/SKILL.md) from the epic branch, through its "Get it green" step. In the PR body, add a closing reference (`Closes #n` on GitHub) for the epic and every issue in the tickets' `Issues`, and list the tickets (title and one line each). Then follow [review-pr](../review-pr/SKILL.md) on the PR. When it kept findings, write them as one new ticket `tickets/NN-review-fixes.md` (each finding an acceptance criterion, its scenario the check), run it through step 6, push the epic branch and get it green again. Then finish ship-pr from its "Merge" step. Done when ship-pr's report is in hand.

8. **Clean up.** Remove any worktrees and ticket branches left. Keep the run folder.

## Report

The PR URL and its state (**merged**, or **waiting** and on whom), one line per ticket with its final status, every `blocked` ticket with its `why`, and the run folder's path.
