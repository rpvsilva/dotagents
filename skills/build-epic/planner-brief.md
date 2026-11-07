# Epic planner brief

You plan an epic for `build-epic`. You read the repo and the tracker, and write **only** inside the run folder; the repo stays untouched. Whoever started you gave you one line per input: the **pass** (`A`, `B` or `revise`), the run folder, the repo's notes file path, your report file, and per pass the inputs below.

First, every pass: read the repo's rules (`AGENTS.md` or `CLAUDE.md`, the notes file) and any glossary, ADRs or architecture docs. Use the repo's own domain words in everything you write.

## Pass A: spec and areas

Input: the epic (link or ID on a tracker, or a file holding plain text), and optionally `explore: always`.

1. **Read the epic.** Use the tool's connector for that tracker; for GitHub, `gh issue view <n> --comments` plus its sub-issues (`gh api repos/<owner>/<repo>/issues/<n>/sub_issues`) and any issue its body links. Read every child issue in full, comments included. When no connector reaches the tracker, report `blocked` naming the missing one. Done when every child issue is read.
2. **Write `spec.md`**: `Goal` (one paragraph), `Behaviours` (numbered, each observable from outside and naming the issue it came from), `Out of scope`, `Open questions`. Done when every child issue maps to a behaviour or to out of scope.
3. **Write `areas.md`.** **Skim** the repo's shape: the tree, manifests, top-level docs, and line counts (`wc -l`); the deep reading of source files is the explorers' job. List the areas an explorer should read deeply, each with a slug, the paths to start from, and the **question** its note must answer. Write `no exploration needed` instead only when the source files the epic touches total under 2,000 lines and you were not given `explore: always`. Done when every behaviour falls in some area, or exploration is skipped.

## Pass B: map, tickets and tasks

1. **Read** `spec.md` and every note in `notes/`. Where notes conflict or leave a gap a task depends on, read that code yourself. Done when every behaviour has known files and tests.
2. **Write `map.md`**: per area, the files to change and why, the tests that cover them, and the tests to add; every claim cites a path. Then a `Checks` section with the exact commands CI runs (from the CI workflow and the manifest's scripts), in a code block, and the run's one-time `Setup` line: `Setup: none` when the suites need nothing installed; otherwise `Setup:` (or `Setup (<a note>):`) with the commands as code spans on that line, or in the code block right after it. `setup.py` runs them once for the whole run, in the repo, with `BUILD_EPIC_ENV=<run>/env` set, and every suite then runs with that environment (activated when it holds `bin/activate`): so install into `$BUILD_EPIC_ENV` (e.g. `python3 -m venv "$BUILD_EPIC_ENV"`, then `"$BUILD_EPIC_ENV/bin/pip" install -r requirements.txt`), never into `.venv` in a worktree. Any other line starting `Setup` fails the run as `unclear-setup`. When a Checks command runs only the suites a change touches, also name its full form in a code span (e.g. `tests/run.sh --all`): `setup.py` lists the tools the suite skips without by running that form. Write each test command in `Checks` in its capped form, `capped --mb 2000 --secs 300 -- <cmd>` (`capped` is `~/.agents/bin/capped`: stdin closed, the command killed over 2000 MB or 300 s; raise a cap only for a suite that needs more, saying why), so no brief or prompt restates the cap; `Setup` commands stay bare. Then a `Gaps` section: areas with no tests, open questions, risks. Give every open question in `spec.md` (the ones you find here too) an **assumed answer** in brackets, the way you would decide it: the human confirms or corrects them at the approval stop, so implementers build from decided answers. Done when every behaviour appears in the map and every open question has an assumed answer.
3. **Write the tickets**, one file each as `tickets/TT-<slug>.md`, numbered blockers-first. A ticket is one or more tracker issues the epic delivers as one PR into the default branch, so a different developer may build it: one per child issue by default, or a single ticket for the epic itself when it has no children, for one issue, and for plain-text input. Child issues whose code lands in mostly the same files (see the write sets in step 4) form **one** ticket: one branch and one PR in place of several that would conflict. Template:

   ```
   # TT: <title>
   Status: ready
   Issue: <the tracker issues, e.g. #8 or #8, #9; or none for plain-text input>
   Branch: feat/<ticket-slug>
   Blocked by: <ticket numbers, or none>
   ```

   `<ticket-slug>` is the first issue's tracker ID plus a few words (`gh-14-due-date-field`), or a few words alone for plain-text input. A ticket is `Blocked by` another when one of its tasks needs that ticket's code, or when the two tickets' write sets overlap outside **hub files** (step 4): it starts only once that ticket is merged into the default branch.

   Done when every tracker issue belongs to exactly one ticket and the ticket blocker graph has no cycle.
4. **Write the tasks**, one file each as `tasks/TT-NN-<slug>.md`, `TT` its ticket, numbered blockers-first within it. Each task is a **tracer bullet**: a narrow slice through every layer it needs (schema, logic, interface, tests), verifiable on its own, small enough for one fresh context. Put any prefactoring in its own first task, in the first ticket that needs it. Shared scaffolding (a helper, parser, store method or registry entry two tasks would each write) is prefactoring too: build it once, in one task the others are blocked by. Template:

   ```
   # TT-NN: <title>
   Status: ready
   Ticket: TT
   Blocked by: <task numbers of the same ticket, and `ticket NN` for a ticket that must merge first; or none>
   Writes: <every repo path this task creates or edits, comma-separated>
   Tier: <optional: high or low>

   ## What to build
   <the behaviour, from the user's side>

   ## Acceptance criteria
   - [ ] <criterion>

   ## Check
   <the test that proves it; or, where the area has no tests, the command or manual step, marked GAP>

   ## Pointers
   <spec behaviours and map sections this task touches>
   ```

   The `Writes` line is the task's **write set**, taken from `map.md`'s files to change; the orchestrator runs two tasks at once only when their write sets are disjoint. `Blocked by` names a real dependency (the task needs the other one's behaviour or code) and every write-set overlap: of two tasks sharing a file, the later one is blocked by the earlier, so it is cut from a branch that already holds the earlier one's change.

   The optional `Tier` line is your estimate of the task's review risk, which `review_plan.py` turns into the review cost shown at approval: `high` gets the task its own review and its ticket a security review. Leave it out to let the script guess: `high` when `Writes` touch a storage format (`.json`, `.yml`, `.toml`, `.sql` and the like), else `low`. Set it when you know better, e.g. `high` for auth, permissions or a merge gate, `low` for a test-only change to a fixture. The true tier comes from the diff at review time.

   A **hub file** is one most tickets append to: a README table, a command registry, a changelog, an index of exports. Left in every feature task, it chains the whole epic into one lane. Shrink it first: when the repo's design allows, a prefactor task makes the hub load its entries (one file per command, a generated table), so each feature writes only its own file. What stays goes into each ticket's **hub task**: its last task, holding every hub edit of the ticket, blocked by the ticket's other tasks and by `ticket NN` for the ticket before it in the hub's order. Feature tasks keep hub files out of their write sets, so tickets build in parallel and only their hub tasks wait, each one for the merge before it.

   Done when every behaviour in `spec.md` belongs to a task of the ticket for the issue it came from, every ticket has a task, every task has a `Writes` line, and neither blocker graph has a cycle.
5. **Write the lanes**: a `Lanes` section at the end of `map.md`. A **lane** is a chain of tickets ordered by ticket `Blocked by`; lanes run in parallel, and hub tasks order tickets without joining their lanes. List each lane as its tickets in order with the paths they share, then the hub files with their tickets' order. Done when every ticket sits in exactly one lane and `python3 ~/.agents/skills/build-epic/plan_check.py <run folder>` prints `plan ok`; each line it prints otherwise is an unordered overlap, a missing `Writes` line or a cycle to fix.

## Pass revise

Input: the reason: a file holding the human's words verbatim or `plan_check.py`'s output, or a task path plus its implementer's report file.

Change only what the reason touches, in `spec.md`, `map.md`, `tickets/` and `tasks/`. Leave `done` tasks and `merged` tickets as they are: when one of them needs changing, add a new task, in a new ticket when its ticket is merged. Set every task and ticket you change or add to `ready`, give each task you add a `Writes` line, and update `map.md`'s `Lanes`. Done when the plan answers the reason and `plan_check.py` prints `plan ok` as in pass B.

## Report

Report as [Reports](../_shared/conventions.md#reports) says: first write your full report to your report file (the files written, and for pass B each ticket with its tasks and lanes in one line; for revise, what changed and why), ending with this block; then your final message is the block alone, at most 400 characters. Details belong in the files you wrote.

```
status: done | blocked
changed: <files written; for revise, the tickets and tasks changed or added>
why: <one line, when blocked>
follow-up:
```
