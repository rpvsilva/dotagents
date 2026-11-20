# dotagents

A portable AI setup: skills, instructions, MCP servers and hooks shared across coding tools.

## Telemetry

**Run**:
The stretch of a session being measured: from the session's start, or from a skill's invocation, until telemetry is requested; includes the subagents started in that stretch. An automatic save at session end measures the whole session, earlier telemetry requests included.
_Avoid_: Execution, trace

**Span**:
One typed skill's stretch within a run, from its invocation to the next typed skill or the telemetry request; skills it loads count toward it. Activity before any skill forms a span called "no skill".
_Avoid_: Segment, column

**Turn**:
One model API call within a run; a reply split across several log lines is still one turn.
_Avoid_: Message, step

**Tool call**:
One tool invocation requested by the model, MCP tools included.
_Avoid_: Action, function call

**Step**:
One tool call in a saved run, with its label, timing, outcome, and the span or subagent that made it. A span holds many steps; a step belongs to exactly one span.
_Avoid_: Action, event

## Configuration

**Setting**:
A named value with a default that a skill reads and acts on, such as how many subagents run at once or which tracker holds tickets.
_Avoid_: Config, option, knob, rule

**Lesson**:
Free-text knowledge about one repo that a skill had to learn the hard way and should know on its next run; it informs judgement and is never a Setting.
_Avoid_: Note, rule, setting

**Notes file**:
The per-repo file holding one skill's Lessons for that repo.
_Avoid_: Config file, settings file

**Level**:
Where a Setting's value comes from. From strongest to weakest: flag (one run), repo (everyone working in that repo), machine (one person across all repos), default (the skill's own).
_Avoid_: Scope, layer, tier

**Secret**:
A credential or token. Never a Setting: it stays in files only its owning command writes.
_Avoid_: Setting, key

**Declaration**:
A skill's list of the Settings it reads: each one's key, kind of value, default and meaning. A Setting no skill declares does not exist.
_Avoid_: Schema, manifest

## Parts

**Part**:
One installable piece of the setup: a skill, a subagent, a mod, a hook, or one of the folder parts `remote` (the `remote-agent` command), `mcp` (the MCP servers) and `global` (the personal instructions, guardrails and plugins). Each names its Group and Needs in a `needs.json` beside it; a subagent's is in the `needs.json` of the skill holding its brief.
_Avoid_: Component, module, package

**Group**:
A named feature made of Parts, which a person can take on its own: `core`, `remote`, `pr-loop`, `epics`, `security`, `safety-hooks`, `agent-flow`, `small-tools` or `personal`. Every Part belongs to exactly one; `groups.json` names them.
_Avoid_: Bundle, set, profile

**Need**:
Another Part a Part refers to, named in its `needs.json`. A required Need is one the Part calls and cannot work without; it always comes along. An optional Need is one the Part works without, such as remote runs it falls back from to local ones; it comes only when named too.
_Avoid_: Dependency, requirement, coupling

**Core**:
The Parts every Group comes with, which no Part names as a Need: the shared conventions (`skills/_shared`), Settings (`~/.agents/bin/settings`), the hooks library and the `configure` skill.
_Avoid_: Base, common, runtime

## Evals

**Eval case**:
One fixed task for one skill, in `evals/<skill>/<case>/`: the prompt a user would type, the fixture repo it starts in, the model and caps it runs with, and the checks its result must pass. Dev tooling, not a Part.
_Avoid_: Test case, benchmark, scenario
