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
}

export type Graph = {
  agents: Agent[]
  /** The main loop's turn count: 1 after the first prompt. */
  turn: number
  /** When the current main turn started (epoch ms); 0 before the first. */
  turnStarted: number
  /** What the last turn's agents came to, shown once a new prompt clears them; null before a second turn. */
  lastTurn: { done: number; failed: number } | null
}

/** How the pane draws: the chart or the tree, and which squad is expanded under the chart (null: none). */
export type View = { tree: boolean; expanded: string | null }

declare module 'claude-code' {
  interface PluginState {
    // Change an atom's shape tag when its fields change: it declines what an earlier version stored.
    'agent-flow': { graph: Shaped<Graph>; view: Shaped<View>; frame: Shaped<number> }
  }
}
