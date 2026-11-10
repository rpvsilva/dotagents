Score the planner's pass A: `spec.md` and `areas.md` in its run folder, judged against the epic in the case prompt's `input` file and the repo's `AGENTS.md`.

- spec-from-epic: Every behaviour in `spec.md` comes from the epic, and every point of the epic is a behaviour or is out of scope; nothing is invented (no feature, flag or format the epic does not ask for).
- behaviours-observable: Each behaviour is numbered and observable from outside: a command, its output, its exit code or the README, not an internal design choice.
- repo-words: The spec uses the repo's own words (count, store, `tally <command>`, the `tally: <message>` error form) rather than new ones.
- assumed-answers: Every open question has an assumed answer in brackets, and each answer is the sensible default a maintainer of this repo would pick.
- areas-sized: `areas.md` fits the repo: the source the epic touches is far under 2,000 lines, so it says `no exploration needed` rather than listing areas.
