# Ship: `ship <TT> open|review|triage|re-review|merge`

## `open`

`ship_ticket.py <run> <TT> open`. You write no part of the PR: the script fills the repo's PR template from the run folder, pushes the ticket branch, puts `worktrees/TT` at its tip and opens the PR into the base, recording its `PR:` line (`OK TT open pr=<n>`; a rerun edits that PR). `FAIL TT push`: tell the human with the log. `FAIL TT empty-template-section <sections>`: the run has nothing for those headings (`Testing`: no task saved a `reports/*.checks.json`); tell the human with the line and the log, and never write the section yourself.

## `review`

The ticket's one deep review, covering all its tasks: follow [review-pr](../../review-pr/SKILL.md) steps 1-5 on the PR with `--mode ticket --run <run> --base origin/<the run's base> --task <ticket file>`: the default reviewer, plus the security reviewer only when the ticket's tier is `high`. A remote reviewer is named `review-<PR>-<sha7>` (`security-<PR>-<sha7>`); once it ends, write `remote-agent result <name>` to `<run>/remote/<name>.json` so telemetry counts its cost. Each review's output lands in `<run>/reviews/<PR>-<sha7>.json` (`-security.json`), and review-pr's step 5 posts it with `post_review.py <run> <PR> <output> --base origin/<the run's base> [--note <your verification>]`, rendered from the output. `FAIL PR<n> post-denied <path>`, or a harness that denies running `post_review.py`: do not retry; the final report names the path.

Then post each review a `Tier: high` task of the ticket had before it merged, the last one per task: `post_review.py <run> <PR> <run>/reviews/TT-NN-<sha7>.json --base <ticket-branch>`; always after the deep review, which must be the first review on the PR.

## `triage`

Sort the confirmed and plausible findings against the ticket's acceptance criteria and the merge rule:

- **Fix**: a `blocker`, or a `should-fix` that breaks an acceptance criterion or a written rule of the repo.
- **Follow up**: everything else. It stays in the review, where the human reads it.

Also count each `follow-up` row (severity `blocker` or `should-fix`) in the `followups` of the ticket's task outputs (`render.py implementer <run>/reports/<TT-NN>.json`) that no later task took as a criterion. With nothing to fix and no such row: `next.py <run> --triaged <TT> none`. Otherwise write one new task `tasks/TT-NN-review-fixes.md` (each Fix finding or such row an acceptance criterion quoted from its source, its scenario the check; `Status: ready`, a `Writes` line), number its Fix findings in `<run>/reviews/<PR>-<sha7>-findings.md`, and run `next.py <run> --triaged <TT> fix`. The task then goes through the build steps like any other; its merge pushes the ticket branch, so the PR shows the fix (description `fix: TT-NN`).

## `re-review`

review-pr steps 1-5 with `--mode re-review --run <run> --base <the head reviewed> --findings <that findings file>`, description `re-review: PR <n>`; then `triage` again. A third round is a `human <TT> review-round-3` line: tell the human with the review's link.

## `merge`

`ship_ticket.py <run> <TT> merge [--reviewed <sha>]` pushes the ticket branch and merges the PR at its tip by the merge rule, closes the ticket's issues when the PR's base is the default branch (into an integration branch it labels them `merged-to-<base>` and leaves them open; first it runs `map.md`'s Checks on the ticket head and checks the tree is the reviewed tree), and removes the ticket's worktree. The reviewed head is the commit of the last posted review (`<run>/logs/PR<n>-review.json`).

- `OK TT merged pr=<n>`: the ticket is `merged`. `OK TT in-review pr=<n>`: the merge rule waits on a human's approval.
- `head-moved`: run it again. `merge`, `push`: tell the human with the log. `no-merge-rule`: the repo gates the default branch and `merge_command` is unset; ask the human to set it, then run it again.
- Refused, nothing merged: `no-review` (post the review), `reviewed-unknown` (check the SHA), `red <suite>` (a Check failed on the ticket head: a fix task as under `triage`), `tree-mismatch` (review the new head), `wrong-base <branch>`, and `guarded ...` as in [build.md](build.md#merge-tt-nn). Tell the human with the log for any but the first.

Then run `tracker_cmd.py <run> close-epic` (it closes the epic's issue once every ticket's is closed), `status.py <run> --write`, and give the human `status.py <run> --line <TT>` as printed. Now compact or clear your context as [the loop](../SKILL.md#the-loop) says.
