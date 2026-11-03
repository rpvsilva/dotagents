# dotagents

Rui's portable AI setup: skills, instructions, MCP servers and hooks shared across coding tools.

## Telemetry

**Run**:
The stretch of a session being measured: from the session's start, or from a skill's invocation, until telemetry is requested; includes the subagents started in that stretch. An automatic save at session end measures the whole session, earlier telemetry requests included.
_Avoid_: Execution, trace

**Span**:
One typed skill's stretch within a run, from its invocation to the next typed skill or the telemetry request; skills it loads count toward it. Activity before any skill forms a span called "no skill".
_Avoid_: Segment, step, column

**Turn**:
One model API call within a run; a reply split across several log lines is still one turn.
_Avoid_: Message, step

**Tool call**:
One tool invocation requested by the model, MCP tools included.
_Avoid_: Action, function call

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
