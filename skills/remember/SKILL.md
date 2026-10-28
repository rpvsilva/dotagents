---
name: remember
description: After a skill run, save what it had to learn about this repo into the repo's .agents/ notes, so the next run starts knowing it.
disable-model-invocation: true
---

Turn what the last run learned the hard way into **lessons** for the next one. A lesson is something the next run would otherwise rediscover or get wrong: a command found by digging, a human's correction, a failure and its real cause, a convention nobody wrote down. Each skill reads its own notes file in the target repo, and those notes override its defaults, so every line you write steers every future run.

Input: optional skill name(s); default: every skill run in this conversation.

## Steps

1. **Find the run.** Name each skill run in this conversation and its notes file: the path its "Learn the rules" step gives (e.g. `.agents/renovate-notes.md` for `renovate-prs`). Done when each skill is paired with a notes file path in the current repo.

2. **Harvest lessons** from the conversation, per skill: commands or paths you had to search for, steps that failed and why, every correction or answer the human gave, guesses that turned out wrong, defaults the repo needed changed (`max_parallel`, a resolve rule, a merge method). Keep a lesson only when it holds for future runs in this repo and nothing in the repo already says it (`AGENTS.md`, CI config, manifest scripts, `--help`): those stay the source of truth. Done when each lesson is one line, tied to the evidence in the conversation.

3. **Route each lesson** to where it belongs:
   - About this skill in this repo: the skill's notes file.
   - About every agent in this repo (a build quirk, a rule for all PRs): the repo's `AGENTS.md`, as a proposal.
   - About the skill itself, in any repo (a step that is wrong or missing): a change to the skill in dotagents, as a proposal.

   Done when every lesson has one destination.

4. **Merge into the notes.** Read each notes file (create it when missing, with a `# <skill> notes` heading). Add each lesson under the heading it fits, replace any line it makes stale, and drop duplicates; keep each file short enough to read in one pass. Done when every lesson is in exactly one place and no line contradicts another.

5. **Show the human** the diff of each notes file, plus each proposal from step 3, and wait. Apply their edits. Done when they approve, or say to drop it.

6. **Ship.** Commit the notes (with any approved `AGENTS.md` change) as one PR by the [ship-pr](../ship-pr/SKILL.md) steps, unless the human asks to leave them uncommitted. Open the dotagents proposals as a separate PR in that repo when the human approves them. Done when each PR URL exists, or the human chose otherwise.

## Report

Per notes file: the lessons added or replaced, one line each. Then the proposals and where they went, and the PR URLs.
