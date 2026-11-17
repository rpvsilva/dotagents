// The mod's state contract, named in plugin.json `types`: self-contained, no import. flow.ts and layout.ts import
// these types from here.

/** An agent's own state. `waiting` is not stored: it is a permission check pending for a while (flow.ts waitingSince). */
export type Status = 'running' | 'done' | 'failed'

export type Agent = {
  /** The agent's id (`agentId` in events); `main` for the session's own loop. */
  id: string
  /** The id of the agent that started it; null for main. */
  parent: string | null
  /** The agent type (`Explore`, `general-purpose`, a plugin's agent); `main` for main. */
  type: string
  /** The short task (the Agent tool's description). */
  desc: string
  /** The resolved model id; null until known. */
  model: string | null
  status: Status
  /** Why it failed: `stopped`, `refusal`, `error`; null otherwise. */
  why: string | null
  /** Epoch ms. */
  started: number
  ended: number | null
  /** Tool calls made. */
  steps: number
  /** Output tokens, from its finished turns. */
  tokens: number
  /** The tool call running now, as one line (`Bash npm test`); null between calls. */
  step: string | null
  /** The tool call that started it (the parent's Agent call); null for main. */
  toolUseId: string | null
  /** The main turn it started in; finished agents of earlier turns leave when a new prompt starts. */
  turn: number
  /** Permission checks pending: tool_use_id → epoch ms it was asked. */
  checks: Record<string, number>
  /** Context used, in tokens, at the last reading (the engine's tokenCount; the session's for main); null before one. */
  ctx: number | null
  /** Its model's context window, in tokens; null before a reading. */
  window: number | null
  /** For a remote-agent run (`remote:<run id>`) and the subagents it ran (`remote:<run id>:<task id>`); null for a local agent. */
  remote: Remote | null
}

/** What a remote run's box and log need beyond an agent's fields. */
export type Remote = {
  runId: string
  /** The run folder, which holds run.json, progress.log, transcript.jsonl, result.json and summary.md. */
  folder: string
  /** The task id of a subagent the run ran; null for the run itself. */
  taskId: string | null
  /** Its subagent's Agent call id in the run's transcript (`parent_tool_use_id` on its own events); null for the run. */
  toolUseId: string | null
  /** The setup phase while the sandbox starts (`claiming`, `sending`, `booting`); null once Claude runs. */
  phase: string | null
  exit: number | null
  cost: number | null
  turns: number | null
  /** Files the run changed (result.json `changed`). */
  files: number | null
  /** Whether a launching Bash call was found for it; when not, it hangs under main. */
  linked: boolean
}

/** A Bash call that ran remote-agent: who made it, when, and the runs it is known to have started. */
export type Launch = {
  toolUseId: string
  agent: string
  at: number
  /** When the call ended; null while it runs. A backgrounded call ends at once. */
  ended: number | null
  /** The `--name` it gave, if any. */
  name: string | null
  /** The Bash call's description, the run's task. */
  desc: string | null
  background: boolean
  /** Run ids its output named (`…/remote-agent/<id>/transcript.jsonl`). */
  runIds: string[]
}

export type Graph = {
  agents: Agent[]
  /** The main loop's turn count: 1 after the first prompt. */
  turn: number
  /** When the current main turn started (epoch ms); 0 before the first. */
  turnStarted: number
  /** What the last turn's agents came to, shown once a new prompt clears them; null before a second turn. */
  lastTurn: { done: number; failed: number } | null
  /** The Bash calls that ran remote-agent, newest last, at most 50. */
  launches: Launch[]
}

/**
 * How the pane draws: the tab (the chart, or the list of agents), the chart or the tree, which squad is expanded
 * under the chart (null: none), and whose log is open on the Agents tab (null: the list).
 */
export type View = {
  tab: 'chart' | 'agents'
  tree: boolean
  expanded: string | null
  agentId: string | null
  /** The Agents tab's filter. */
  filter: Filter
  /** The squad groups open in the Agents list, by group id (`parent:type`). */
  groups: string[]
  /** The logs to go back to, oldest first, before the tab the first log was opened from (`origin`). */
  trail: string[]
  origin: 'chart' | 'agents'
}

export type Filter = 'all' | 'running' | 'needs' | 'failed' | 'done'

/** One line of an agent's log, by kind: its prompt, its text, a tool call, a tool's result or error. */
export type LogLine = { kind: 'prompt' | 'text' | 'call' | 'result' | 'error' | 'agent' | 'note'; text: string; agentId?: string }

/** The open log: its lines, the last message read, and the window the pane shows. */
export type Log = {
  /** Whose log; null when none is open. */
  agentId: string | null
  lines: LogLine[]
  /** The uuid of the last message read: the next read starts after it; null before the first read. */
  after: string | null
  /** Whether the window shows the end and moves with it. */
  follow: boolean
  /** The first line the window shows when it does not follow. */
  top: number
  /** Whether older lines were left out. */
  truncated: boolean
  /** Why the session would not give the messages; null when it did. */
  denied: string | null
}

declare module 'claude-code' {
  interface PluginState {
    // Change an atom's shape tag when its fields change: it declines what an earlier version stored.
    'agent-flow': { graph: Shaped<Graph>; view: Shaped<View>; frame: Shaped<number>; log: Shaped<Log> }
  }
}
