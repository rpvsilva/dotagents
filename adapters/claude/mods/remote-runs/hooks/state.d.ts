// The mod's state contract, named in plugin.json `types`: self-contained, no import.

/** A run folder of this repo: `<git dir>/remote-agent/<id>/`. */
export type Run = { id: string; folder: string }

declare module 'claude-code' {
  interface PluginState {
    // Change an atom's shape tag when its fields change: it declines what an earlier version stored.
    'remote-runs': { runs: Shaped<Run[]> }
  }
}
