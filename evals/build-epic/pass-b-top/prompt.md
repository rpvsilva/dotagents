Set up the run folder first: `mkdir -p {scratch}/notes {scratch}/reports && cp {input}/run/spec.md {input}/run/areas.md {scratch}/`. Pass A is done: those are its spec and areas, and it found no exploration needed, so `notes/` stays empty.

You are an epic-planner. Follow ~/.agents/skills/build-epic/planner-brief.md. The repo is your working folder, {work}.
{
 "pass": "B",
 "run": "{scratch}",
 "notes": "{work}/.agents/build-epic-notes.md",
 "output": "{scratch}/reports/plan-B.json"
}
