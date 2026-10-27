---
name: security-audit
description: Security-audit a whole repo of any size: a map split into areas and flows, one reviewer per piece, gap sweeps until nothing new; then fix the findings the human picks in a PR.
disable-model-invocation: true
---

Find every real security hole in a repo in one run, prove each one, and close the ones the human picks. A clean repo is a valid result: report only what you can prove. Divide and conquer, with nothing but subagents: a mapper splits the repo into **areas** and cross-area **flows**, one fresh reviewer takes each piece, **sweeps** hunt the gaps the coverage tables leave, you **verify** each finding, and every fix lands through a PR. For one PR, use [review-pr](../review-pr/SKILL.md): it runs the same reviewer.

You are the **orchestrator**: keep your context **thin**. You read the run folder's short files (`map.md`'s tables, `findings.md`, one-line reports), not the code; the one exception is verifying findings. Every heavy read happens in a subagent that writes a file and returns one line.

Input: nothing for the current repo, or `owner/name` for another. Optional: `--parallel N`.

## Run folder

`~/.agents/runs/<repo>/security-audit-<YYYY-MM-DD>/`. It holds `worktree/`, `map.md`, `reports/<scope>-r<round>.md` (one per reviewer per round), `tickets/` and `worktrees/` for fixes, and `findings.md`: your ledger, one row per finding (number, status `confirmed`/`plausible`/`dropped`/`fixed`/`declined`, severity, file:line, claim, gain, fix, report it came from), then an **Other bugs** list: the non-security bugs the reports name. When the folder exists, **resume** at the first step whose done-condition is not met.

## Subagents

| Agent | Brief | Writes |
|---|---|---|
| `security-mapper` | [mapper-brief.md](mapper-brief.md) | `map.md` |
| `security-reviewer` | [security-brief.md](security-brief.md) | `reports/<scope>-r<round>.md` |
| `epic-implementer` | [implementer-brief.md](../build-epic/implementer-brief.md) | fix commits on its ticket branch |

Start the named agent if your harness defines it, otherwise a general-purpose subagent told to follow the brief. Hand over inputs as **paths**, never summaries, so each read is not anchored on yours. If your harness has no subagents, follow each brief yourself, one at a time, writing the same files.

At most **N** subagents run at once (the **slots**): `--parallel N`, else `max_parallel` from the notes file, else 4, the same default as build-epic.

## Steps

1. **Learn the rules.** Read `.agents/security-audit-notes.md`, the repo's `AGENTS.md` (or `CLAUDE.md`), `CONTRIBUTING.md` and the PR template if they exist. Note the **identity** the rules name for GitHub calls (e.g. a `GH_TOKEN` prefix for a bot account), the local **checks** command, the **accepted risks**, `max_parallel` and `area_budget` (default 4000 lines) from the notes. If the rules name a setup that is missing, stop and ask. Done when you know each of these.

2. **Set up.** Create the run folder and check out the code in it: `git fetch origin && git worktree add --detach <run>/worktree origin/<default-branch>`. Clone first, into a temporary folder, when the repo is not local. Seed `findings.md` with the accepted risks as `declined`. Done when the worktree's HEAD is the remote default branch.

3. **Map.** Start a `security-mapper` with the worktree path, the rule files, the path `<run>/map.md` and the area budget. Done when `map.md` exists and places every file in an area, the repo-wide lane, or not reviewed.

4. **Hunt.** Start one `security-reviewer` per area, per flow, and one for the repo-wide lane, while a slot is free. Give each: the worktree path, the rule files, its scope and the `map.md` path, `findings.md` as known findings, and the report path `reports/<scope>-r1.md`. Done when every reviewer has returned its line and its report has a complete coverage table; start a fresh one for a scope whose table is not.

5. **Verify.** Read each new report. Merge the findings into `findings.md`, keeping one per file:line and claim. For each new one, open the code it cites and confirm the attack really works: trace the input from source to sink, or run it in the worktree. Then hold its **gain** against the attacker's **baseline** in `map.md`'s threat model. Mark it `confirmed`, `plausible` (could not settle) or `dropped`. Keep it only when the path holds end to end and the gain is real: the attacker reads, writes, runs or blocks something their baseline does not reach, on a setup the repo's users plausibly run with the OS defaults left on (e.g. Linux's `protected_hardlinks`). Move a finding whose only effect is a bug with no gain to **Other bugs**. With more than 10 new findings, split them into batches and start subagents that verify by this same rule and return a status per finding. Done when no row lacks a status.

6. **Sweep.** Start one fresh `security-reviewer` with scope **gaps**: the worktree path, the rule files, the `map.md` path, the reports folder, `findings.md` as known findings, and the report path `reports/gaps-r<round>.md`. Verify what it returns as in step 5. Repeat while a sweep adds a `confirmed` or `plausible` `blocker` or `should-fix`, up to three sweeps. Done when a sweep adds none, or after the third, saying so. A sweep that finds nothing is the expected end, not a failure.

7. **Ask what to fix.** Show the kept findings as a numbered list, most severe first, one line each: severity, file:line, claim, gain, the proposed fix, and the plausible ones marked; then **Other bugs** as a separate list numbered `B1`, `B2`..., marked as not security; then the rounds run. Give your recommendation: which findings are worth their fix, and which other bugs, if any, belong in this PR. Ask which to fix: the recommended ones, some by number, or none. With no kept findings, say what was checked and stop. Done when the human has answered. Mark the rest `declined`; on "none", ask whether to record them as accepted risks: on yes, run step 8 with nothing to fix; on no, go to step 9.

8. **Fix in a PR.** In the worktree, `git switch -c fix/security-<short-name>`: the **fix branch**. Append to `map.md` a `## Setup` section (what the checks need first) and a `## Checks` section (the checks from step 1). For each area, or the repo-wide lane, with chosen findings, write `<run>/tickets/NN-<scope>.md` in build-epic's ticket shape ([planner-brief.md](../build-epic/planner-brief.md), step 3): one acceptance criterion per finding, "the attack in finding #N fails", proven by a test that performs the attack and goes **red** before the fix, when the repo tests that kind of code (a chosen other bug: "bug BN no longer happens", proven the same way); `Pointers` names the `findings.md` rows and their reports. Then, while a slot is free, `git worktree add <run>/worktrees/NN -b fix/security-<short-name>/NN fix/security-<short-name>` and start an `epic-implementer` with the ticket path, the run folder, that worktree path and the fix branch name.
   - **On `done`**: `git merge --squash fix/security-<short-name>/NN`, commit, run the checks. When the merge conflicts or a check goes **red**, undo it (`git reset --hard`, or `git reset --hard HEAD~1`) and send the ticket back to its implementer with the failure. When green, mark its findings `fixed` and remove its worktree and branch.
   - **On `blocked` or `plan-wrong`**: leave its findings open with the `why`, and show it to the human in the report.

   Add each `declined` finding to the **accepted risks** in `.agents/security-audit-notes.md`, one line with file:line and claim, so the next audit skips it. Then ship the fix branch with the [ship-pr](../ship-pr/SKILL.md) steps, listing each fixed finding and its test in the PR body. Done when every ticket is merged or reported, every merged finding is `fixed` with its test on a pushed branch, and the PR URL exists.

9. **Clean up**: `git worktree remove` each worktree under `<run>/`. Keep the run folder.

## Report

What was reviewed (areas, flows, repo-wide, not reviewed), the rounds, the kept findings (fixed, declined or open), the PR URL with its state, and the run folder's path.
