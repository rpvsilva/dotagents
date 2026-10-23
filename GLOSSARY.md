# dotagents

Rui's portable AI setup: skills, instructions, MCP servers and hooks shared across coding tools.

## Telemetry

**Run**:
The stretch of a session being measured: from the session's start, or from a skill's invocation, until telemetry is requested; includes the subagents started in that stretch.
_Avoid_: Execution, trace

**Turn**:
One model API call within a run; a reply split across several log lines is still one turn.
_Avoid_: Message, step

**Tool call**:
One tool invocation requested by the model, MCP tools included.
_Avoid_: Action, function call
