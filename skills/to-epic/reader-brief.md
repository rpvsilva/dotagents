# Source reader brief

You read one source for `to-epic` and write one note. You read; you write **only** your note. Whoever started you gave you: the source (a path or a link) and the note's path. Read links with whatever connector or fetch tool your harness has; when none reaches the source, report `blocked` naming it.

1. **Read the whole source**, start to end. Done when you have read every part, including appendices, comments and action items.
2. **Write the note** with these sections; every item quotes or cites the passage it came from (section, timestamp, or a short quote):
   - `Needs`: what users or the business want, one per line, in the source's own words where possible.
   - `Decisions`: what was settled, and by whom when the source says.
   - `Constraints`: deadlines, limits, things that must not change.
   - `Out of scope`: what the source rules out.
   - `Open questions`: what the source leaves undecided or contradicts.

   Done when every need, decision and open question in the source appears in the note, or the section says `none`.

## Report

Your final message is this block and nothing else; details belong in the note.

```
status: done | blocked
changed: <the note's path>
why: <one line, when blocked>
```
