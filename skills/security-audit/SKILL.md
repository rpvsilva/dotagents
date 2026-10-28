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

Start and limit them as in [Subagents](../_shared/conventions.md#subagents): at most **N** at once (the **slots**).

## Steps

1. **Learn the rules** ([how](../_shared/conventions.md#learn-the-rules); notes file `.agents/security-audit-notes.md`). Besides the identity, note the local **checks** command, the **accepted risks**, `max_parallel` and `area_budget` (default 4000 lines) from the notes. Done when you know each of these.

2. **Set up.** Create the run folder and check out the code in it: `git fetch origin && git worktree add --detach <run>/worktree origin/<default-branch>`. Clone first, into a temporary folder, when the repo is not local. Seed `findings.md` with the accepted risks as `declined`. Done when the worktree's HEAD is the remote default branch.

3. **Map.** Start a `security-mapper` with the worktree path, the rule files, the path `<run>/map.md` and the area budget. Done when `map.md` exists and places every file in an area, the repo-wide lane, or not reviewed.

4. **Hunt.** Start one `security-reviewer` per area, per flow, and one for the repo-wide lane, while a slot is free. Give each: the worktree path, the rule files, its scope and the `map.md` path, `findings.md` as known findings, and the report path `reports/<scope>-r1.md`. Done when every reviewer has returned its line and its report has a complete coverage table; start a fresh one for a scope whose table is not.

5. **Verify.** Read each new report. Merge the findings into `findings.md`, keeping one per file:line and claim. For each new one, open the code it cites and confirm the attack really works: trace the input from source to sink, or run it in the worktree. Then hold its **gain** against the attacker's **baseline** in `map.md`'s threat model, by the reporting rule in [security-brief.md](security-brief.md) step 3. Mark it `confirmed`, `plausible` (could not settle) or `dropped`: drop it when the path breaks anywhere or the gain fails that rule. Move a finding whose only effect is a bug with no gain to **Other bugs**. With more than 10 new findings, split them into batches and start subagents that verify by this same rule and return a status per finding. Done when no row lacks a status.

6. **Sweep.** Start one fresh `security-reviewer` with scope **gaps**: the worktree path, the rule files, the `map.md` path, the reports folder, `findings.md` as known findings, and the report path `reports/gaps-r<round>.md`. Verify what it returns as in step 5. Repeat while a sweep adds a `confirmed` or `plausible` `blocker` or `should-fix`, up to three sweeps. Done when a sweep adds none, or after the third, saying so. A sweep that finds nothing is the expected end, not a failure.

7. **Ask what to fix.** Show the kept findings as a numbered list, most severe first, one line each: severity, file:line, claim, gain, the proposed fix, and the plausible ones marked; then **Other bugs** as a separate list numbered `B1`, `B2`..., marked as not security; then the rounds run. Give your recommendation: which findings are worth their fix, and which other bugs, if any, belong in this PR. Ask which to fix: the recommended ones, some by number, or none. With no kept findings, say what was checked and stop. Mark the rest `declined`. On "none", also ask whether to record them as accepted risks; on no, go to step 10. Done when the human has answered.

8. **Fix.** Skip this step when nothing was picked. In the worktree, `git switch -c fix/security-<short-name>`: the **fix branch**. Append to `map.md` a `## Setup` section (what the checks need first) and a `## Checks` section (the checks from step 1). For each area, or the repo-wide lane, with chosen findings, write `<run>/tickets/NN-<scope>.md` in build-epic's ticket shape ([planner-brief.md](../build-epic/planner-brief.md), step 3): one acceptance criterion per finding, "the attack in finding #N fails", proven by a test that performs the attack and goes **red** before the fix, when the repo tests that kind of code (a chosen other bug: "bug BN no longer happens", proven the same way); `Pointers` names the `findings.md` rows and their reports. Then, while a slot is free, `git worktree add <run>/worktrees/NN -b fix/security-<short-name>/NN fix/security-<short-name>` and start an `epic-implementer` with the ticket path, the run folder, that worktree path and the fix branch name.
   - Handle each implementer's answer as build-epic's step 6 does (**On `done`**, **On `blocked`**, **On `plan-wrong`**; [SKILL.md](../build-epic/SKILL.md)), with the fix branch as the epic branch and these changes: on `done`, mark the ticket's findings `fixed`; on `blocked` or `plan-wrong`, leave its findings open with the `why` for the report, with no re-plan; skip every tracker update.

   Done when every ticket is merged into the fix branch or reported, and every merged finding is `fixed` with its test.

9. **Record and ship.** On the fix branch (create it as in step 8 when step 8 was skipped), add each `declined` finding to the **accepted risks** in `.agents/security-audit-notes.md`, one line with file:line and claim, so the next audit skips it. Then ship the fix branch with the [ship-pr](../ship-pr/SKILL.md) steps, listing each fixed finding and its test in the PR body. Done when the PR URL exists.

10. **Clean up**: `git worktree remove` each worktree under `<run>/`. Keep the run folder.

## Report

What was reviewed (areas, flows, repo-wide, not reviewed), the rounds, the kept findings (fixed, declined or open), the PR URL with its state, and the run folder's path.
