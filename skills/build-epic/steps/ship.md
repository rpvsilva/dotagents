# Ship: `ship <TT> open|review|post|triage|re-review|merge`

`next.py <run> --go` runs `open` and `merge` itself and prints their lines; the sections below say what those lines mean. With remote on, it also starts a `review` or `re-review`'s reviewers itself (`remote <TT> review started name=<name> log=<file>`; see [Remotely](#remotely)). When a ticket's reviewer returns (a remote one: its output copied to `<run>/reviews/<PR>-<sha7>.json` first), `next.py <run> --reviewed <TT> <output> [--base <ref>] [--note <text>]` posts it, prints it rendered for the triage, and goes on as `--go`.

## `open`

`ship_ticket.py <run> <TT> open`. You write no part of the PR: the script fills the repo's PR template from the run folder, pushes the ticket branch, puts `worktrees/TT` at its tip and opens the PR into the base, recording its `PR:` line (`OK TT open pr=<n>`; a rerun edits that PR). `FAIL TT push`: tell the human with the log. `FAIL TT empty-template-section <sections>`: the run has nothing for those headings (`Testing`: no task saved a `reports/*.checks.json`); tell the human with the line and the log, and never write the section yourself.

## `review`

The ticket's one deep review, covering all its tasks: follow [review-pr](../../review-pr/SKILL.md) steps 1-5 on the PR with `--mode ticket --run <run> --base origin/<the run's base> --task <ticket file>`: the default reviewer, plus the security reviewer only when the ticket's tier is `high`. A remote reviewer is named `review-<PR>-<sha7>` (`security-<PR>-<sha7>`); once it ends, write `remote-agent result <name>` to `<run>/remote/<name>.json` so telemetry counts its cost. Each review's output lands in `<run>/reviews/<PR>-<sha7>.json` (`-security.json`), and `next.py <run> --reviewed <TT> <output> [--note <your verification>]` posts it (review-pr's step 5, `post_review.py`), rendered from the output. `FAIL PR<n> post-denied <path>`, or a harness that denies running `post_review.py`: do not retry; the final report names the path. `FAIL PR<n> brief-violation <problems>`: the output broke its schema and nothing was posted; continue the reviewer once naming the problems, as the [Protocol](../../_shared/conventions.md#protocol) says.

### Remotely

With remote on, `next.py --go` does review-pr steps 2 and 3 **Remotely** itself for `review` and `re-review`: a worktree `<run>/worktrees/review-<PR>-<sha7>` at the PR's head, `tier.py` (a ticket review), `review_prompt.py --remote <run>/reviews/<PR>-<sha7>[-security]-remote/` with `--allow-author` the human's login and the ticket's `Opened by:` login, and `remote-agent` detached as `review-<PR>-<sha7>` (and `security-<PR>-<sha7>` when the tier is high). The ticket then shows `wait <TT> review remote`. `next.py --wait` collects each one that ends: `remote-agent result <name>` into `<run>/remote/<name>.json`, its report copied to `<run>/reviews/<PR>-<sha7>.json` (`-security.json`), checked (`OK reviewer done`) and rendered, and the worktree removed once both ended. Then it prints `ship <TT> post pr=<n> output=<file> [base=<head reviewed>]`: verify each finding (review-pr step 4), then `next.py <run> --reviewed <TT> <output> [--note <your verification>]` for each output, adding `--base <head reviewed>` only when the line has `base=` (a re-review): a first review's diff starts from the run's base, which `--reviewed` uses by default. `FAIL <TT> remote-review <why>` (its prompt refused, e.g. a PR by another author) or `FAIL <TT> remote-exit <code>: ...` (the run failed or left no report): `next.py` keeps a `<run>/remote/review-<PR>-<sha7>.local` marker and prints the `review` line again; run that review here, as review-pr step 3 says without **Remotely**.

`--reviewed` on the ticket's first deep review then posts the last review of each `Tier: high` task of the ticket (`post_review.py <run> <PR> <run>/reviews/TT-NN-<sha7>.json --base <ticket-branch>`, its line printed), after the deep review, which must be the first review on the PR.

### Here

With remote off, or after a remote refusal, `next.py --go` prepares the reviewers itself, as **Remotely** does but with `--output` in place of `--remote`: the worktree, `tier.py` on a ticket review, and a prompt per reviewer in `<run>/prompts/<review|security>-<PR>-<sha7>-local.md`, each printed as `agent <TT> <prompt> agent=<pr-reviewer|security-reviewer> description=<ticket-review|re-review|security>: PR <n>`. Start each. The ticket shows `wait <TT> review here` until every output exists; then `ship <TT> post`, with each output checked and rendered once. `FAIL <TT> review-prep <why>`: prepare that review by hand, as review-pr steps 2 and 3 say.

## `triage`

Sort the confirmed and plausible findings against the ticket's acceptance criteria and the merge rule:

- **Fix**: a `blocker`, or a `should-fix` that breaks an acceptance criterion or a written rule of the repo.
- **Follow up**: everything else. It stays in the review, where the human reads it.

Also count each `follow-up` row (severity `blocker` or `should-fix`) in the `followups` of the ticket's task outputs (`python3 -I ../_shared/protocol/render.py implementer <run>/reports/<TT-NN>.json`) that no later task took as a criterion. With nothing to fix and no such row: `next.py <run> --triaged <TT> none`. Otherwise write one new task `tasks/TT-NN-review-fixes.md` (each Fix finding or such row an acceptance criterion quoted from its source, its scenario the check; `Status: ready`, a `Writes` line), number its Fix findings in `<run>/reviews/<PR>-<sha7>-findings.md`, and run `next.py <run> --triaged <TT> fix`. The task then goes through the build steps like any other; its merge pushes the ticket branch, so the PR shows the fix (description `fix: TT-NN`).

## `re-review`

review-pr steps 1-5 with `--mode re-review --run <run> --base <the head reviewed> --findings <that findings file>`, description `re-review: PR <n>`; then `triage` again. A third round is a `human <TT> review-round-3` line: tell the human with the review's link.

## `merge`

`ship_ticket.py <run> <TT> merge [--reviewed <sha>]` pushes the ticket branch and merges the PR at its tip by the merge rule, closes the ticket's issues when the PR's base is the default branch (into an integration branch it labels them `merged-to-<base>` and leaves them open; first it runs `map.md`'s Checks on the ticket head and checks the tree is the reviewed tree), and removes the ticket's worktree. The reviewed head is the commit of the last posted review (`<run>/logs/PR<n>-review.json`).

- `next.py --go` leaves `ship TT merge` to you when the run has a clause the human did not waive and `origin/<default>` has no `agent-merge-guard` block ([merge-guard.md](../merge-guard.md)): the guard then passes every diff, so its instruction names those clauses for you to check the PR against before you run `ship_ticket.py` yourself.
- `OK TT merged pr=<n>`: the ticket is `merged`. `OK TT in-review pr=<n>`: the merge rule waits on a human's approval.
- `head-moved`: run it again. `merge`, `push`: tell the human with the log. `no-merge-rule`: the repo gates the default branch and `merge_command` is unset; ask the human to set it, then run it again.
- Refused, nothing merged: `no-review` (post the review), `reviewed-unknown` (check the SHA), `red <suite>` (a Check failed on the ticket head: a fix task as under `triage`), `tree-mismatch` (review the new head), `wrong-base <branch>`, and `guarded ...` as in [build.md](build.md#merge-tt-nn). Tell the human with the log for any but the first.

Then run `tracker_cmd.py <run> close-epic` (it closes the epic's issue once every ticket's is closed), `status.py <run> --write`, and give the human `status.py <run> --line <TT>` as printed. Now compact or clear your context as [the loop](../SKILL.md#the-loop) says.
