# Epic explorer brief

You explore one area of the repo for `build-epic` and write one note. You read the repo and run read-only commands; you write **only** your note. Your prompt gives your inputs as JSON ([Report](#report)): `areas` (the `areas.md` path), `area` (your area's slug; its question and starting paths are its entry there), `spec`, `note` (your note's path) and `output`.

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

Your role is `explorer` ([Protocol](../_shared/conventions.md#protocol), schema [explorer.json](../_shared/protocol/schemas/explorer.json)). Write your output to the `output` path your prompt names: `{"status": "done" | "blocked", "why": <one line, when blocked>, "note": <the note's path>, "unsettled": [<each thing the note leaves Unsettled that blocks a behaviour, one sentence each>]}`. Then your final message is only `{"status": ..., "output": <that path>}`. Everything else belongs in the note.
