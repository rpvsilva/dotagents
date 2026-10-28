---
name: ship-pr
description: Take the current changes to a merged PR by the repo's own branch, commit, check and merge rules.
disable-model-invocation: true
---

Ship the current changes as one PR, the way this repo wants PRs shipped. The repo's rules win over every default below.

## Steps

1. **Learn the rules** ([how](../_shared/conventions.md#learn-the-rules); notes file `.agents/ship-pr-notes.md`). Besides the identity, note:
   - **Commit convention** (e.g. Conventional Commits), else match `git log`.
   - **Checks**: the local command that runs what CI runs.
   - **Merge rule**: whether an agent may merge, and how (a merge script, required checks, approval tiers). With no rule, the agent does not merge.

   Done when you can name the identity and each of the three, or know the repo is silent on it.

2. **Branch.** If on the default branch, `git switch -c <type>/<short-name>`. Done when HEAD is a new branch holding only the changes for this PR. When unrelated changes are mixed in, ask which ones belong.

3. **Check.** Run the checks from step 1 and fix what fails. Done when they all pass.

4. **Commit and push.** Commit with the repo's convention, then `git push -u origin <branch>`. Done when the branch is on the remote.

5. **Open the PR** with the identity from step 1. Fill every section of the template: under testing, the commands you ran and their results. Done when the PR URL exists and no template section is empty.

6. **Get it green.** `gh pr checks <n> --watch`. On a **red** check, start the PR fixer agent if your harness defines one, otherwise follow [fix-pr](../fix-pr/SKILL.md) yourself; then watch again. Done when every check passes, or a failure remains that needs a human (a secret, an approval).

7. **Merge** only when the merge rule from step 1 lets an agent merge this PR, using exactly the method it names. Done when the PR shows `MERGED`, or is green and waiting on a named person.

8. **Clean up** after a merge: `git switch <default-branch> && git pull --ff-only`, delete the local branch.

## Report

The PR URL and its state: **merged**, or **waiting** and on whom (approval, a failing check you could not fix and why).
