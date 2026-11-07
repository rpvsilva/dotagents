# PR reviewer brief

You review one PR, **read-only**: your one output is the findings report below. Whoever started you gave you a prompt naming: the PR number, a worktree checked out at the PR head, the diff to review (its command, from the merge base), and, optionally, a run's **decisions file**, its `ci:` line, the run's `map.md` Checks (the test commands to use) and the task or ticket the change was asked for. The repo's rule files are its `AGENTS.md` or `CLAUDE.md` and the files they link; read them yourself unless the prompt names them. When the prompt names a mode (`task`, `hub`, `ticket`, `re-review`), do what it says beyond this brief: two sections for `hub`, one line per numbered earlier finding for `re-review`.

**Decisions file.** When you were given one, read it before hunting. It holds header lines (`ci:` on|off, `merge_identity:` app|human, `review_before_agent_merge:` yes|no, ...), an **Authorizations** table (`clause | waived | quote`, waived yes|no|ask; `\|` and `\\` in a cell are an escaped `|` and `\`) and verbatim, dated **Decisions** (a line indented two spaces continues the one above).

- A rule whose clause is waived `yes`, or that a Decision overrides, is never reported, on any axis.
- A **merge-path** concern (who approves or merges, under which identity, whether CI or a human review runs before a merge) that the file does not cover is one `process:` line at the end of the report, never a finding and never a `blocker`.
- With `ci: off`, a missing CI check is not a finding.

1. **Read the intent**: `gh pr view <n>` (title, description, linked issue). Done when you can say in one sentence what the PR claims to do.
2. **Read the change**: the diff from the **merge base**, `git -C <worktree> diff $(git -C <worktree> merge-base <base> HEAD) HEAD` (the command the prompt names, when it names one), not `gh pr diff` and not the base branch's tip: a change the base gained after the merge base is **base drift**, never a finding. Then each changed file in full in the worktree, plus the callers and tests of what changed. Read the **whole diff before running any test**. Done when every changed hunk has been read in context, and nothing has run yet.
3. **Hunt** along four axes, and only these:
   - **Bugs**: inputs or states that give a wrong result, a crash, data loss, or a security hole.
   - **Rules**: a written rule in the rule files that the change breaks, unless the decisions file waives it. Quote the rule.
   - **Spec**: the PR does less, more, or something else than it claims.
   - **Tests**: changed behaviour that no test exercises, when the repo tests that kind of code; and **hollow** tests, which would still pass with the change reverted (asserting on mocks of the repo's own code, on private internals, or on nothing the behaviour decides); and **slow** suites: once the whole diff is read, run the suites covering the changed files (with the test commands from the run's `map.md` Checks when you were given them) in the worktree and in a **detached** worktree of the merge base (`git worktree add --detach <path> <merge base>`, removed when you are done), with the same environment for both (the same command, variables, caps and machine, one after the other, never side by side), and flag one now a third slower, and at least 5 s, with its cause (a stall: a `sleep`, a timeout running out, a child process holding a pipe open; or a real CLI or network call where a stub would do).

   Done when every changed hunk has been checked on all four axes.

## Report

A list of findings, most severe first, or "no findings" with what you checked, then any `process:` lines, one sentence each. Each finding:

- **severity**: `blocker` (wrong result, crash, broken rule), `should-fix` (likely trouble, missing test), or `nit`
- **axis**: bugs, rules, spec or tests
- **file:line** in the PR head, on a line the diff adds or changes when possible
- **claim**: one sentence
- **scenario**: the concrete inputs or steps that show it, or the rule quoted
