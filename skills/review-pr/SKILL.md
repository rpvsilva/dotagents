---
name: review-pr
description: First-pass PR review by fresh subagents, at the depth the change's risk tier calls for (a security reviewer only on high tier); verified findings posted as one comment-only review.
disable-model-invocation: true
---

Give a PR its first review before a human looks: a fresh reviewer finds problems, you **verify** each one, and only the ones that hold up get posted. The review is always comment-only: approval belongs to a human.

Input: the PR number. Optional: `--dry-run` to show the findings without posting; `--mode <mode>`, the review's depth (default `ticket`); `--base <ref>`, what the change is reviewed against (default `origin/<the PR's base branch>`; for `hub`, the ticket's own base, since the PR's base is the ticket branch; for `re-review`, the head last reviewed); `--run <run>`, a `build-epic` run folder, whose `decisions.md` (which of the repo's leave-for-human rules the human waived, and their verbatim decisions), `ci:` line and `map.md` Checks the reviewers read; and the files a mode needs, below.

**Modes**, each rendered into the reviewer's prompt by `review_prompt.py` (step 3):

| Mode | Reviews | Reviewer | Needs |
|---|---|---|---|
| `task` | one task PR, against its base | light | `--task <file>` optional |
| `hub` | A: the hub task PR against `--task-base` (the ticket branch); B: the whole ticket against the base, in one run | default | `--ticket <file>`, `--task-base <ref>`, `--task <file>` optional |
| `ticket` | the whole PR, once and in depth | default | `--task <file>` optional (the ticket file) |
| `re-review` | only the diff since the head last reviewed (`--base`), checking a numbered list of earlier findings | light | `--findings <file>` |
| `security` | the PR for security, beside a `hub` or `ticket` review of a high-tier change | security | none |

The **light** reviewer is the PR reviewer on a small model (the `pr-reviewer-light` agent where your harness defines one); where it has no light agent, use the default PR reviewer.

## Steps

1. **Learn the rules** ([how](../_shared/conventions.md#learn-the-rules); notes file `.agents/review-pr-notes.md`). Done when you know which files hold the repo's rules and which identity to post as.

2. **Check out the PR head** in its own git worktree: `git fetch origin pull/<n>/head && git worktree add --detach <path> FETCH_HEAD`. Done when the worktree's HEAD matches `gh pr view <n> --json headRefOid`.

3. **Start the reviewers**, each a subagent with a fresh context, in parallel.
   - **Tier the change**, in `hub` and `ticket` modes: `python3 -I ~/.agents/skills/review-pr/scripts/tier.py --git <worktree> $(git -C <worktree> merge-base <base> HEAD)`, `<base>` the `--base` ref. Its first line is the **risk tier**, `low` or `high`; the lines under it are the signals that decided it. (`tier.py --trim <diff file>` cuts a diff to its changed files and added lines: that is how the fixtures under `scripts/fixtures/` were made from the gh-35 PRs.)
   - **Render each prompt**: `python3 -I ~/.agents/skills/review-pr/scripts/review_prompt.py <mode> --pr <n> --worktree <worktree> --head <head sha> --base <ref> [--run <run>] [--task <file>] [--ticket <file> --task-base <ref>] [--findings <file>]`, with the options its mode needs (Modes above; `hub` refuses to run without `--ticket` and `--task-base`, `re-review` without `--findings`). `--base` is the `--base` ref above. Write its output to a file and give that output, as printed, as the reviewer's whole message: it points at the brief, the diff and the run's files (the brief has the reviewer find the repo's rule files itself), and holds no opinion of the PR or summary of it, so the reviewer's read is not anchored on yours. Never write or add to a prompt yourself.
   - **PR reviewer**, always, in the mode given: the light reviewer for `task` and `re-review`, the default one for `hub` and `ticket` (the matching PR reviewer agent if your harness defines one, otherwise a general-purpose subagent told to follow [reviewer-brief.md](reviewer-brief.md)).
   - **Security reviewer**, only in `hub` and `ticket` modes, and only when the tier is `high`: the `security` mode's prompt (`--base` as for the PR reviewer), to the security reviewer agent if your harness defines one, otherwise a general-purpose subagent told to follow [security-brief.md](../security-audit/security-brief.md). A `low` tier gets none, whatever the files are; a `task` or `re-review` run never gets one, since its ticket's deep review decides.

   Without `--run`, the prompts name no decisions file, and the briefs apply every rule as written. If your harness has no subagents, follow each prompt and its brief yourself in turn, before reading anything else about the PR. Done when every reviewer started has returned its findings.

4. **Verify.** Merge the findings, keeping one per file:line and claim. For each finding, open the code it cites and confirm the failure scenario really happens: trace the inputs, or run the code or a test. Drop what does not hold; keep what you could not settle as **plausible**. Done when every finding is **confirmed**, **plausible** or dropped.

5. **Post** one review as the identity from step 1, with `event` always `COMMENT`: write the payload to a file and send it with `gh api repos/<owner>/<repo>/pulls/<n>/reviews --input <file>`. Payload: `{"commit_id": <head sha>, "event": "COMMENT", "body": <summary>, "comments": [{"path", "line", "side": "RIGHT", "body"}]}`, one inline comment per kept finding on the line it cites. The summary says how many findings, by severity, and marks plausible ones. With no findings, post a short body saying what was checked. With `--dry-run`, show the payload instead. Done when the review URL exists, or the payload is shown.

6. **Clean up**: `git worktree remove <path>`.

## Report

The review URL (or the dry-run payload) and the kept findings, most severe first, one line each.
