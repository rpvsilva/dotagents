# Approve: `approve run`

1. **Claims.** Run `tracker_cmd.py <run> claims`: it sets each `ready` ticket whose issue is assigned to someone other than the human to `claimed`, with a `Claimed by:` line. (With the `tracker` Setting naming another tracker, its line ends `manual tracker=<name>`: check the assignees yourself through that tracker's connector.)
2. **Clauses.** List every **leave-for-human clause** in the repo's rules: each rule that keeps an action for a human (an approval, a merge, a push to a protected branch, an approval-tier path in `.review-gate.yml` or `CODEOWNERS`), quoted. Each merge guard rule `view.py` lists (format in [merge-guard.md](../merge-guard.md)) is one more clause, named as printed; when the repo has a guard block, or a task's `Writes` adds one, add the built-in clause `merge-guard` too (a diff that changes the rules block itself). `FAIL run bad-rule: <line>` from `guard_rules.py` means every merge will refuse: tell the human with the line.
3. **Show the plan**: `view.py <run> plan`, as printed. Under it, add only your judgement:
   - the **approval cost**: how many ticket PRs the merge rule leaves to a human (e.g. ones touching an approval-tier path), and which tickets wait on those merges; with a base other than the default branch, the one gated PR left after the run instead (the base into the default branch) and the approval-tier paths it will carry;
   - the **Authorizations**, asked as below.
4. **Wait for the answer.** Record each clause's answer once with `decide.py <run> clause "<clause>" yes|no|ask "<the human's words>"` (only `yes` lets a merge carry a guard rule's hits) and any other decision with `decide.py <run> quote`. Set each ticket the human leaves to someone else to `claimed`. On a rejection, run [`plan revise`](plan.md#plan-revise) with their words, then show the plan again.
5. **Once approved**: write `<run>/plan/TT.md` for each ticket the answer decided something about (the answers to `spec.md`'s open questions that bear on it, any change to its scope), run `tracker_cmd.py <run> planned` (one plan comment per ticket issue), then `decide.py <run> set approved yes`.

## Asking for authorizations

A clause quoted from a rules file names paths and calls, not what the run will do. Ask about each one so the human can answer without opening anything:

1. Number it, and give it a plain name of a few words ("Changes to the tests folder").
2. **Guards:** one sentence on what it keeps for the human and the risk behind it.
3. **This run:** which tickets or tasks will hit it, judged from their `Writes` lines, or "not expected". Then what each answer means for those: `yes`, agents merge them on their own; `no`, each stops until the human merges it, and every ticket behind it waits too; `ask`, the run pauses at each hit and asks.
4. **Recommended:** your answer and the one reason for it.
5. **Rule:** the clause word for word, last, on its own line; for a guard rule, its name, kind and pattern as printed.

Put the clauses this run is not expected to hit together at the end, under "Not expected to come up", with one recommended answer for all. Close with one line the human can copy and edit as their reply, such as `1 yes, 2 ask, 3 yes`, and say that the answers hold for this run only.
