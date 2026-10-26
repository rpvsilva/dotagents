---
name: review-pr
description: First-pass review of a PR by a fresh reviewer: bugs, the repo's written rules, and whether it does what it says; verified findings posted as one comment-only review. Never approves.
disable-model-invocation: true
---

Give a PR its first review before a human looks: a fresh reviewer finds problems, you **verify** each one, and only the ones that hold up get posted. The review is always comment-only: approval belongs to a human.

Input: the PR number. Optional: `--dry-run` to show the findings without posting.

## Steps

1. **Learn the rules.** Read `.agents/review-pr-notes.md`, the repo's `AGENTS.md` (or `CLAUDE.md`) and `CONTRIBUTING.md` if they exist. Note the **identity** the rules name for GitHub calls (e.g. a `GH_TOKEN` prefix for a bot account); if the rules name a setup that is missing, stop and ask. Done when you know which files hold the repo's rules and which identity to post as.

2. **Check out the PR head** in its own git worktree: `git fetch origin pull/<n>/head && git worktree add --detach <path> FETCH_HEAD`. Done when the worktree's HEAD matches `gh pr view <n> --json headRefOid`.

3. **Start the reviewer.** Start one subagent with a fresh context: the PR reviewer agent if your harness defines one, otherwise a general-purpose subagent told to follow [reviewer-brief.md](reviewer-brief.md). Give it the PR number, the worktree path and the rule files from step 1. Hand over **only** those: no opinion of the PR, no summary of it, so its read is not anchored on yours. If your harness has no subagents, follow the brief yourself, before reading anything else about the PR. Done when it returns its findings.

4. **Verify.** For each finding, open the code it cites and confirm the failure scenario really happens: trace the inputs, or run the code or a test. Drop what does not hold; keep what you could not settle as **plausible**. Done when every finding is **confirmed**, **plausible** or dropped.

5. **Post** one review as the identity from step 1, with `event` always `COMMENT`: write the payload to a file and send it with `gh api repos/<owner>/<repo>/pulls/<n>/reviews --input <file>`. Payload: `{"commit_id": <head sha>, "event": "COMMENT", "body": <summary>, "comments": [{"path", "line", "side": "RIGHT", "body"}]}`, one inline comment per kept finding on the line it cites. The summary says how many findings, by severity, and marks plausible ones. With no findings, post a short body saying what was checked. With `--dry-run`, show the payload instead. Done when the review URL exists, or the payload is shown.

6. **Clean up**: `git worktree remove <path>`.

## Report

The review URL (or the dry-run payload) and the kept findings, most severe first, one line each.
