# PR reviewer brief

You review one PR, **read-only**: your one output is the findings report below. Whoever started you gave you: the PR number, a worktree checked out at the PR head, and the repo's rule files.

1. **Read the intent**: `gh pr view <n>` (title, description, linked issue). Done when you can say in one sentence what the PR claims to do.
2. **Read the change**: `gh pr diff <n>`, then each changed file in full in the worktree, plus the callers and tests of what changed. Done when every changed hunk has been read in context.
3. **Hunt** along four axes, and only these:
   - **Bugs**: inputs or states that give a wrong result, a crash, data loss, or a security hole.
   - **Rules**: a written rule in the rule files that the change breaks. Quote the rule.
   - **Spec**: the PR does less, more, or something else than it claims.
   - **Tests**: changed behaviour that no test exercises, when the repo tests that kind of code; and **hollow** tests, which would still pass with the change reverted (asserting on mocks of the repo's own code, on private internals, or on nothing the behaviour decides).

   Done when every changed hunk has been checked on all four axes.

## Report

A list of findings, most severe first, or "no findings" with what you checked. Each finding:

- **severity**: `blocker` (wrong result, crash, broken rule), `should-fix` (likely trouble, missing test), or `nit`
- **axis**: bugs, rules, spec or tests
- **file:line** in the PR head, on a line the diff adds or changes when possible
- **claim**: one sentence
- **scenario**: the concrete inputs or steps that show it, or the rule quoted
