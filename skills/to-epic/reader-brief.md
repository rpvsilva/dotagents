# Source reader brief

You read one source for `to-epic` and write one note. You read; you write **only** your note and your output. Your role is `reader` ([Protocol](../_shared/conventions.md#protocol); schema [reader.json](../_shared/protocol/schemas/reader.json)): your prompt's input object holds `source` (a path or a link), `note` (the note's path) and maybe `output`. Read links with whatever connector or fetch tool your harness has; when none reaches the source, return `blocked` naming it.

1. **Read the whole source**, start to end. Done when you have read every part, including appendices, comments and action items.
2. **Write the note** with these sections; every item quotes or cites the passage it came from (section, timestamp, or a short quote):
   - `Needs`: what users or the business want, one per line, in the source's own words where possible.
   - `Decisions`: what was settled, and by whom when the source says.
   - `Constraints`: deadlines, limits, things that must not change.
   - `Out of scope`: what the source rules out.
   - `Open questions`: what the source leaves undecided or contradicts.

   Done when every need, decision and open question in the source appears in the note, or the section says `none`.

## Output

Return the `reader` output object as [Protocol](../_shared/conventions.md#protocol) says: with an `output` input, write the object there as JSON, then your final message is only `{"status": "<status>", "output": "<that path>"}`; with none, your final message is the object itself, and nothing else. Before your final message, run `python3 -I ~/.agents/skills/_shared/protocol/check.py reader output <file>` on the object and fix each problem it prints until it prints `OK reader <status>`: with an `output` file, on that file; with none (a remote run), write the object to a file outside the worktree (`/tmp/reader-output.json`) first. Details belong in the note.

- `status`: `done`, or `blocked` with `why` (one line, at most 300 characters): the source you could not reach or read.
- `note`: the note's path.

For example: `{"status": "done", "note": "/home/me/.agents/runs/shop/to-epic-checkout/sources/kickoff.md"}`.
