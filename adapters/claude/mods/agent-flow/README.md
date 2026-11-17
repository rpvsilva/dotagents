# agent-flow

A Claude Code mod: `/agent-flow` opens a pane that draws this session's agents as a live org chart. `main` sits at the top, centred; each subagent hangs in a box under the agent that started it, joined by a connector. The pane opens only by the command, never by itself, so it shows at any terminal width.

It only observes. Every hook passes its call on with `next(e)`, unchanged, and records what happened; each hook that wraps a call (`agent.spawn`, `tool.call`, `tool.check`) has a `.catch` that passes the call on, so a failure in the mod can cost the chart an event but never fails, delays or changes a call. It makes no network requests and runs no processes.

## What a box shows

Each box (24 columns) shows the agent's status glyph and type, its task (the Agent tool's description), its model (`opus 5.5`, from the resolved id; Bedrock and Vertex ids read the same) with the time it has run, and one last line:

- **running**: a spinner in the model's colour, and the tool call running now (`⎿ Bash npm test`, the input field that says what it does, control characters dropped), or `⎿ thinking` between calls;
- **needs you** (`◐`, warning colour): a permission check pending at least 1 s. Quicker checks, which the auto-mode classifier settles, never show;
- **done** (`✓`, dim): its steps (tool calls) and time;
- **failed** (`✗`, error colour): why: `stopped` (aborted), `refusal` or `error`, from its turn's end, or `error` when the Agent call that started it errored.

`main` shows `waiting on agents`, `thinking` or `idle`, its model (learned from the first spawn) and the turn count.

Colours: status from the theme (`warning`, `success`, `error`, `inactive`), so light and colour-blind themes keep working; the model's family colours a running box: `claude` for opus, `suggestion` for sonnet, teal for haiku, `merged` for fable.

## When boxes come and go

- A new box enters over 0.6 s: its connector grows from the parent, then the border draws, then the content.
- A running box stays. A finished one lingers (option `linger_s`, 30 s), then dims with a dotted border, shrinks to a one-line chip (`╶ ✓ Explore 0:42 ╴`), and goes, over 1 s.
- A failed box stays until the next turn starts, then leaves like a finished one once it has lingered. A box with a child still shown stays until that child has gone.
- A new turn never cuts a linger short (a background agent's notification starts a turn too); the header then says what the last turn came to (`last turn: ✓ 4 ✗ 1`).
- `main` never leaves and never moves: everything is placed relative to it.
- When the layout changes, every box glides from where it is to its new spot on one shared 320 ms ease-out, so a row moves as a whole; a pass then keeps at least 2 columns between neighbours in every row, so no two boxes ever touch.

## Many agents

- **Squads**: 4 or more siblings with the same parent and type (option `squad_min`) fold into one squad box, 48 columns wide however many: the type and count, the model, one cell per agent in launch order (its status glyph; a gap every 5; past 36, `+N`), a progress bar (done, running, waiting, left) with counts. `x` (or the Expand button) lists the members under the chart, one line each, in up to 3 columns.
- **Folding**: when a row is wider than the pane, finished siblings fold into one `✓ N done` box, the parent with the most children first. Running and failed boxes never fold.
- **Tree**: when even folded the chart does not fit, the pane draws an indented tree instead, one line per agent (and its current step under it), and says how many columns the chart needs. It goes back to the chart only once the chart has fitted with 4 columns to spare for 2 s, so a chart on the edge never flickers between the two. `t` switches between the chart and the tree whenever the chart fits.

## The rest of the pane

Over the chart: the counts of this turn (`3 running · 1 need you · 4 done · 1 failed`) and the **needs you** strip, naming every agent waiting for you or failed this turn (two, then `+N more`), wherever it sits, never hidden in a squad. Under it: the Tree/Chart and Expand buttons, each with a hotkey (quick repeated clicks select text in Claude Code and are lost, keys never are), and what the keys do.

The status line, set only when its text changes: `● 3 running · ◐ 1 needs you · ✗ 1 failed`; cleared when no agent runs, waits or failed this turn.

A timer runs every 125 ms. It redraws the pane only while something on it moves (an agent running, a box entering, gliding or leaving, a box lingering towards its leave) and the pane was drawn in the last 2 s, so a closed or still pane costs nothing. Spinners, the dot that runs along a live connector, and the glide are terminal-only; on other surfaces, or with option `motion` off, the chart is still.

The graph lives in `$.state`, so a hot reload keeps it; at most 100 agents, the oldest finished ones dropped first. Agents from before the mod loaded are not shown.

## Options

| Option | Default | Meaning |
|---|---|---|
| `linger_s` | 30 | Seconds a finished agent's box stays before it leaves |
| `squad_min` | 4 | Siblings of one agent type that fold into one squad box |
| `motion` | `on` | `on`: spinners, moving dots and gliding boxes; `off`: a still chart |

These are the mod's own plugin options (`/config`), not dotagents Settings, so the mod works on a machine without dotagents.

`./install.sh claude agent-flow` links this folder to `~/.claude/skills/agent-flow`, where Claude Code loads it as a plugin and reloads it on every save.

## Files

- `hooks/main.tsx`: the hooks module. Records events, runs the timer, sets the status line, draws the pane.
- `hooks/flow.ts`: the agent graph, built from events by one pure reducer; summaries, model labels and colours, the status line text.
- `hooks/lifecycle.ts`: when a box enters, lingers and leaves.
- `hooks/layout.ts`: the chart on a character grid (squads, folding, the shared tween, the minimum gap, connectors), the strips, the expanded squad and the tree.
- `hooks/state.d.ts`: the state contract (`plugin.json` `types`) and the shared types. Change an atom's `shape` tag when its fields change.
- `hooks/*.unit.ts`: the logic under `node --test`, including a frame scan that plays two sessions (five agents with a fork; a 24-agent fan-out) frame by frame and fails if two boxes ever touch.
- `hooks/main.test.tsx`: engine tests under `claude plugin test`, with the engine's answers faked.

## Tests

```sh
bash tests/mod.test.sh                                     # every mod: the below
node --test adapters/claude/mods/agent-flow/hooks/*.unit.ts
claude plugin validate adapters/claude/mods/agent-flow
claude plugin test adapters/claude/mods/agent-flow
```
