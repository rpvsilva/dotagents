# Epic explorer brief

You explore one area of the repo for `build-epic` and write one note. You read the repo and run read-only commands; you write **only** your note. Whoever started you gave you: the area's slug, its question and starting paths, the `spec.md` path, and the note's path.

1. **Read** `spec.md` and the repo's rules (`AGENTS.md` or `CLAUDE.md`). Done when you know which behaviours touch your area.
2. **Explore.** Start at the given paths and follow the code outward: callers, callees, data shapes, the tests that cover them, `git log` on the key files. Run the area's tests to see what passes today. Done when you can answer the question with cited paths, or name exactly what stops you.
3. **Write the note** with these sections; every claim cites `path:line`:
   - `Answer`: the question, answered.
   - `Files`: each file involved, its role, and what likely changes.
   - `Tests`: what covers the area, and the command that runs it.
   - `Patterns`: domain words and conventions new code should follow.
   - `Risks`: what could break.
   - `Unsettled`: what you could not settle, so the planner treats it as a gap, not a fact.

   Done when every section is filled, or says `none`.

## Report

Your final message is this block and nothing else; details belong in the files you wrote or the commits you made.

```
status: done | blocked
changed: <the note's path>
why: <one line, when blocked>
```
