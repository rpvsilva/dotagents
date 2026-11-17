// The agent-flow mod: `/agent-flow` opens a pane drawing the session's agents as an org chart, live. It only
// observes: every hook passes its call on with next(e), unchanged, and records what happened through flow.ts's
// reducer. Logic sits in plain .ts files (flow.ts, lifecycle.ts, layout.ts); this module only calls `$` and draws.
// A failure to record or draw is caught: it never fails, delays or changes a tool call.
import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import { emptyGraph, type FlowEvent, MAIN, reduce, statusText, summarize } from './flow.ts'
import { chart, fallback, headerRow, needsRow, newTween, noFallback, type Row, type Seg, TWEEN_MS, tree } from './layout.ts'
import { isMoving } from './lifecycle.ts'
import type { View } from './state.d.ts'

const PANE = 'agent-flow'
const TICK_MS = 125
// A pane not drawn for this long is taken as closed: the timer stops redrawing it until something changes.
const DRAWN_MS = 2000
const KEYS = 't tree/chart · x expand squad · esc close'

const graph = atom({ plugin: 'agent-flow', key: 'graph' } as const, emptyGraph(), { shape: 'graph-1' })
const view = atom({ plugin: 'agent-flow', key: 'view' } as const, { tree: false, expanded: null } as View, { shape: 'view-1' })
const frame = atom({ plugin: 'agent-flow', key: 'frame' } as const, 0, { shape: 'frame-1' })

// Per-process state, which a reload starts again: where boxes were drawn, the status line last set, the last draw.
let tween = newTween()
let fall = noFallback()
let lastStatus: string | undefined | null = null
let lastDraw = 0
let isTicking = false
let settings = { lingerMs: 30_000, squadMin: 4, motion: true }

async function now($: EngineInterface): Promise<number> {
  try {
    return await $.clock.now()
  } catch {
    return 0
  }
}

/** Sets the status line only when its text changed. */
async function status($: EngineInterface): Promise<void> {
  const text = statusText(await read($, graph), await now($))
  if (text === lastStatus) return
  lastStatus = text
  $.ui.status(text)
}

/** Records one event; never throws. */
async function record($: EngineInterface, ev: FlowEvent): Promise<void> {
  try {
    await update($, graph, g => reduce(g, ev))
    await status($)
  } catch {
    // the chart misses this event; the call it came from goes on as it would
  }
}

/** Redraws the pane while something moves on it and it was drawn lately; keeps the status line current. */
async function tick($: EngineInterface): Promise<void> {
  if (isTicking) return
  isTicking = true
  try {
    const t = await now($)
    await status($)
    const isDrawn = t - lastDraw < DRAWN_MS
    const isGliding = t - tween.t0 < TWEEN_MS
    if (isDrawn && (isGliding || fall.fitSince !== null || isMoving(await read($, graph), t, settings.lingerMs))) await update($, frame, n => n + 1)
  } catch {
    // no redraw this tick
  } finally {
    isTicking = false
  }
}

const numberOr = (v: unknown, d: number, min: number) => (typeof v === 'number' && Number.isFinite(v) && v >= min ? v : d)

export const register: Register = (on, options) => {
  const o = (options ?? {}) as Record<string, unknown>
  settings = {
    lingerMs: numberOr(o.linger_s, 30, 0) * 1000,
    squadMin: Math.round(numberOr(o.squad_min, 4, 2)),
    motion: o.motion !== 'off',
  }

  on('session.start', async ($, e, next) => {
    await $.command.register({ name: 'agent-flow', description: "Show this session's agents as a live org chart in a pane" })
    $.clock.every(TICK_MS, () => {
      void tick($)
    })
    return next(e)
  })

  on('command.run', { command: 'agent-flow' }, async $ => {
    const { isPlaced } = await $.ui.open({ id: PANE, title: 'Agent flow', focus: true, closeOnEscape: true })
    return { text: isPlaced ? 'Agent flow pane opened.' : 'Agent flow pane opens once the terminal is wider.' }
  })

  on('turn.start', async ($, e, next) => {
    await record($, { kind: 'turnStart', at: await now($), agent: e.agentId ?? MAIN })
    return next(e)
  })

  on('turn.complete', async ($, e, next) => {
    const out = (e.usage as { output_tokens?: number } | undefined)?.output_tokens
    await record($, { kind: 'turnEnd', at: await now($), agent: e.agentId ?? MAIN, reason: e.reason, outputTokens: typeof out === 'number' ? out : null })
    return next(e)
  })

  on('agent.spawn', async ($, e, next) => {
    const r = await next(e)
    if (r.deny === undefined && r.agentId) {
      await record($, {
        kind: 'spawn', at: await now($), id: r.agentId, parent: e.parentAgentId ?? MAIN, type: e.subagentType, desc: e.description,
        model: r.model ?? null, parentModel: e.parentModel ?? null, toolUseId: e.tool_use_id || null,
      })
    }
    return r
  }).catch(($, e, next) => next(e))

  on('tool.call', async ($, e, next) => {
    const agent = e.agentId ?? MAIN
    await record($, { kind: 'tool', at: await now($), agent, tool: e.tool, summary: summarize(e.tool, e as unknown as Record<string, unknown>) })
    const r = await next(e)
    await record($, { kind: 'toolEnd', at: await now($), agent })
    if (e.tool === 'Agent' && e.tool_use_id && (r.deny !== undefined || r.isError)) {
      await record($, { kind: 'spawnFailed', at: await now($), toolUseId: e.tool_use_id, why: 'error' })
    }
    return r
  }).catch(($, e, next) => next(e))

  on('tool.check', async ($, e, next) => {
    const agent = e.agentId ?? MAIN
    const id = e.tool_use_id
    if (id) await record($, { kind: 'check', at: await now($), agent, toolUseId: id })
    const r = await next(e)
    if (id) await record($, { kind: 'checkEnd', at: await now($), agent, toolUseId: id })
    return r
  }).catch(($, e, next) => next(e))

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const { Box, Text, Button } = $.ui.resolve(e)
    await read($, frame) // redrawn on each frame the timer counts
    const g = await read($, graph)
    const v = await read($, view)
    const t = await now($)
    lastDraw = t
    const columns = Math.max(40, e.props.bodyColumns - 2)
    const opts = { columns, lingerMs: settings.lingerMs, squadMin: settings.squadMin, motion: settings.motion && e.surface === 'terminal', expanded: v.expanded }
    const c = chart(g, t, opts, tween)
    fall = fallback(fall, c.width, columns, t)
    const asTree = v.tree || fall.tree
    const body = asTree ? tree(g, t, opts) : c.rows
    const seg = (s: Seg, i: number) => (
      <Text key={`s${i}`} {...(s.color ? { color: s.color } : {})} {...(s.dim ? { dimColor: true } : {})} {...(s.bold ? { bold: true } : {})}>
        {s.text}
      </Text>
    )
    const line = (r: Row, i: number) => (
      <Text key={`r${i}`} wrap="truncate-end">
        {r.length ? r.map(seg) : ' '}
      </Text>
    )
    const squad = c.squads[0]
    return (
      <Box flexDirection="column" paddingX={1}>
        <Box flexDirection="column" alignItems="center" marginBottom={1}>
          {line(headerRow(g, t), 0)}
          {line(needsRow(g, t), 1)}
        </Box>
        {body.map(line)}
        <Box columnGap={2} marginTop={1}>
          {fall.tree && !v.tree ? (
            <Text key="view" dimColor>{`tree: the chart needs ${c.width + 2} columns`}</Text>
          ) : (
            <Button key="view" hotkey="t" dimColor onPress={() => update($, view, x => ({ ...x, tree: !x.tree }))}>
              {asTree ? 'Chart view' : 'Tree view'}
            </Button>
          )}
          {squad && !asTree && (
            <Button key="expand" hotkey="x" dimColor onPress={() => update($, view, x => ({ ...x, expanded: x.expanded === squad.id ? null : squad.id }))}>
              {v.expanded === squad.id ? `Collapse ${squad.label}` : `Expand ${squad.label}`}
            </Button>
          )}
          <Text dimColor wrap="truncate-end">{KEYS}</Text>
        </Box>
      </Box>
    )
  })
}
