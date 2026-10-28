---
name: address-review
description: Work through a PR's open review threads: fix what holds up, reply to the rest, push; disagreements go to the human first. Never approves or merges.
disable-model-invocation: true
---

Answer every open review thread on one PR. Each thread ends **fixed** (a pushed commit, cited in a reply), **answered** (a reply that settles a question), or **pushed back** (a reply with the evidence the comment does not hold). A reviewer is a person waiting on you, so every reply the human has not seen is one they would want to see first when it disagrees.

Input: the PR number. Optional: `--dry-run` to stop after the triage.

## Steps

1. **Learn the rules** ([how](../_shared/conventions.md#learn-the-rules); notes file `.agents/address-review-notes.md`). Besides the identity, note the commit convention, the local checks command, and whether the repo lets the author **resolve** threads (default: resolve your own fixed threads, leave the rest to the reviewer). Done when you can name the identity, the checks command and the resolve rule.

2. **Check out the branch** in its own git worktree: `git fetch origin && git worktree add <path> -B <branch> origin/<branch>`. Done when `git status` shows the PR branch at its remote head.

3. **Collect the threads.** Fetch every review thread with `gh api graphql` (`pullRequest.reviewThreads`: `id`, `isResolved`, `isOutdated`, `path`, `line`, and each comment's author, body and URL), plus the bodies of submitted reviews (`gh pr view <n> --json reviews`). Keep a thread when it is unresolved and its last comment is not from the identity in step 1; keep a review body when it asks for something no thread covers. Done when you hold a list of open items, each with its file:line (or none) and the full conversation.

4. **Triage.** For each item, open the code it cites and check the claim against it: trace the inputs, or run the code or a test. Then sort it:
   - **fix**: the claim holds and the change belongs in this PR.
   - **answer**: a question, or a request already met; the reply points at the code that settles it.
   - **push back**: the claim does not hold, or the change belongs outside this PR; the reply carries the evidence (the trace, the test, the rule).
   - **decide**: needs a call only the human can make (scope, design, two reviewers asking for opposite things).

   Done when every item has a class and, for **push back**, the evidence written down. With `--dry-run`, show the triage and stop.

5. **Check with the human.** Show every **push back** and **decide** item with its drafted reply or the options, and wait. Apply their answers: a push back they reject becomes a **fix**; a decision becomes a **fix** or a reply. Skip this step when there are none. Done when no item is left as **decide**.

6. **Fix.** One commit per **fix** item (or per group of items asking for one change), test-first when the change alters behaviour: a test that goes **red** on the issue the comment names, then **green**. Run the checks from step 1. Done when every fix is committed and the checks pass.

7. **Push and reply.** Push the branch, then post as the identity, one reply per item on its own thread (`addPullRequestReviewThreadReply`; a top-level PR comment for review bodies): for **fix**, what changed and the commit SHA; for the others, the reply from step 4 or 5. Resolve threads by the rule from step 1 (`resolveReviewThread`). Done when every item has a reply URL.

8. **Get it green.** `gh pr checks <n> --watch`. On a **red** check, start the PR fixer agent if your harness defines one, otherwise follow [fix-pr](../fix-pr/SKILL.md) yourself; then watch again. Done when every check passes, or a failure remains that needs a human.

9. **Clean up**: `git worktree remove <path>`.

Approving and merging belong to others: a human approves, and [ship-pr](../ship-pr/SKILL.md) or the repo's merge rule merges.

## Report

One line per item: its class, the thread URL, and the commit SHA or a short gist of the reply. Then the PR's check state, and who it now waits on (the reviewers to look again).
