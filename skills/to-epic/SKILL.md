---
name: to-epic
description: Turn meeting notes, docs or any other source into an epic and its issues on the tracker, after one human approval of the draft.
disable-model-invocation: true
---

Turn source material into an **epic** and its **issues** on the tracker. Issues describe **outcomes**: what the user gets and why, with acceptance criteria and scope. The how (files, code, slicing into tasks) belongs to whoever builds the epic, such as [build-epic](../build-epic/SKILL.md)'s planner.

Input: any mix of pasted text, file paths and links (docs, Notion, Google Docs, ...).

## Run folder

`~/.agents/runs/<repo>/to-epic-<slug>/`: `<repo>` is the repo folder's name, `<slug>` a few words naming the epic. It holds `sources/<name>.md` (one note per large source) and `draft.md`.

## Steps

1. **Set up.** [Learn the rules](../_shared/conventions.md#learn-the-rules) with notes file `.agents/to-epic-notes.md` and the `tracker`, `labels` and `issue_template` Settings, falling back to the repo's issue templates (`.github/ISSUE_TEMPLATE/`) and existing labels. Create the run folder. Done when you can name the tracker, the identity and the labels (or know there are none).

2. **Read the sources.** Read short text (under about 100 lines: a pasted paragraph, a page of notes) yourself. For each longer source, and each link, start a `source-reader` subagent with [reader-brief.md](reader-brief.md) as in [Subagents](../_shared/conventions.md#subagents); give it the source (path or link) and `sources/<name>.md` as its output, and run them in parallel. Done when every source is read or has a note.

3. **Check the tracker** for open issues that already cover part of the material (search by the key terms of each need). Done when each need is matched to an existing issue or marked new.

4. **Draft `draft.md`**:
   - **Epic**: title, `Goal`, `Why`, `Scope`, `Out of scope`.
   - **Issues**, each an outcome a user would notice: title, what and why, `Acceptance criteria` (checkboxes), `Blocked by` (other issues in the draft), `Source` (the passage it came from: file and section, link, or a short quote). No file paths, code or implementation steps. An existing issue from step 3 appears as `reuse #<n>` with one line on why it fits.
   - **Open questions**: each with the answer you assumed in the draft.

   Done when every need from the sources lands in an issue, in `Out of scope`, or in an open question.

5. **Approve.** Show the human: the epic's goal, each issue (title, blocked by, reused or new), the open questions with your assumed answers, and the path to `draft.md`. With more than about 10 issues, propose splitting into several epics. Wait for the answer; on a change, update `draft.md` and show it again. Done when the human approves.

6. **Create** with the identity from step 1, in blocker order so each issue can name real numbers:
   1. The epic as a parent issue.
   2. Each new issue, with `Blocked by #<n>` in its body and the labels from step 1. Write the body from the draft; keep the `Source` line.
   3. Link every issue (new and reused) as a sub-issue of the epic; on GitHub, `gh api -X POST repos/<owner>/<repo>/issues/<epic>/sub_issues -F sub_issue_id=<issue id>` (the `id`, not the number). Add native blocking links where the tracker has them (on GitHub, `gh api -X POST repos/<owner>/<repo>/issues/<n>/dependencies/blocked_by -F issue_id=<blocker id>`); when that call fails, the body's `Blocked by` line stands alone.

   Done when the epic lists every issue as a sub-issue.

## Report

The epic's link, one line per issue (number, title, new or reused, blocked by), and the next step: `/build-epic <epic link>`.
