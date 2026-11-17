// The chart: the agent graph laid out as an org chart on a character grid, one Row of styled segments per terminal
// row. Pure but for the Tween it is handed (where boxes were last drawn), which it updates. Tested by
// layout.unit.ts under node --test, the frame scan included: no two boxes ever closer than GAP columns.
//
// main sits at the top, centred, and never moves; each agent hangs under the one that started it; 4+ siblings of one
// type fold into a squad box; when a row is still too wide, its finished siblings fold into `✓ N done`, and when even
// that does not fit, the caller draws `tree()` instead.

import { type Agent, type Graph, MAIN, modelColor, modelLabel, printable, shownStatus } from './flow.ts'
import { ENTER_MS, phaseOf, type Phase, schedule } from './lifecycle.ts'

export type Seg = { text: string; color?: string; bold?: boolean; dim?: boolean }
export type Row = Seg[]

/** Where each box was drawn, so a layout change glides from there: one shared ease-out over TWEEN_MS. */
export type Tween = { sig: string; from: Record<string, number>; t0: number; disp: Record<string, number> }
export const newTween = (): Tween => ({ sig: '', from: {}, t0: 0, disp: {} })

export type ChartOpts = { columns: number; lingerMs: number; squadMin: number; motion: boolean; expanded: string | null }
export type Chart = { rows: Row[]; squads: { id: string; label: string }[]; fits: boolean; moving: boolean; width: number }

export const W = 24 // an agent's box
export const SW = 48 // a squad's box
export const H = 6
export const GAP = 2
const VG = 3 // rows between a box and its children's: stub, bar, arrow
export const TWEEN_MS = 320
const SPIN = '⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏'
const FAINT = 'inactive'

type Node = {
  id: string
  kind: 'agent' | 'squad' | 'fold'
  parent: string | null
  type: string
  model: string | null
  start: number
  agent?: Agent
  members?: Agent[]
  phase: Phase
}

const pad = (s: string, n: number) => (s.length > n ? s.slice(0, n - 1) + '…' : s + ' '.repeat(n - s.length))
export const clock = (ms: number) => {
  const s = Math.max(0, Math.floor(ms / 1000))
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}
const nodeW = (n: Node) => (n.kind === 'squad' ? SW : W)
/** A record's number for `k`, 0 when it has none: positions and depths are always set before they are read. */
const num = (r: Record<string, number>, k: string) => r[k] ?? 0

/** The spinner frame at `now`, offset by `i` so a squad's cells do not tick in step; a dot when motion is off. */
export const spin = (now: number, motion: boolean, i = 0) => (motion ? SPIN.charAt((Math.floor(now / 125) + i) % SPIN.length) : '●')

/** A squad's members, in launch order, as one pseudo-agent for the lifecycle rules. */
function squadAgent(members: [Agent, ...Agent[]]): Agent {
  const running = members.some(m => m.status === 'running')
  const failed = members.some(m => m.status === 'failed')
  return {
    ...members[0],
    id: `squad:${members[0].parent}:${members[0].type}`,
    status: running ? 'running' : failed ? 'failed' : 'done',
    started: members[0].started,
    ended: running ? null : Math.max(...members.map(m => m.ended ?? 0)),
    turn: Math.max(...members.map(m => m.turn)),
    checks: {},
    why: null,
  }
}

/** The tree of nodes shown at `now`: agents by parent, squads folded, `+N done` folds where `foldUnder` says. */
function nodes(g: Graph, now: number, opts: ChartOpts, foldUnder: Set<string>): Map<string, Node[]> {
  const sched = schedule(g, opts.lingerMs)
  const byParent = new Map<string, Node[]>()
  const childrenOf = (id: string) => g.agents.filter(a => a.parent === id)
  const walk = (id: string) => {
    const kids = childrenOf(id).sort((x, y) => x.started - y.started)
    const out: Node[] = []
    const types = [...new Set(kids.map(a => a.type))]
    for (const type of types) {
      const same = kids.filter(a => a.type === type)
      if (same.length >= opts.squadMin && same[0]) {
        const sq = squadAgent(same as [Agent, ...Agent[]])
        const ph = phaseOf(sq, now, schedule({ ...g, agents: [...g.agents, sq] }, opts.lingerMs))
        if (ph) out.push({ id: sq.id, kind: 'squad', parent: id, type, model: same[0].model, start: sq.started, members: same, phase: ph })
        continue
      }
      for (const a of same) {
        const ph = phaseOf(a, now, sched)
        if (!ph) continue
        out.push({ id: a.id, kind: 'agent', parent: id, type: a.type, model: a.model, start: a.started, agent: a, phase: ph })
        walk(a.id)
      }
    }
    // Folding: the finished, not failed, agents under this parent become one `✓ N done` box, their children with them.
    if (foldUnder.has(id)) {
      const done = out.filter(n => n.kind === 'agent' && n.agent!.status === 'done' && !byParent.get(n.id)?.length)
      if (done.length >= 2) {
        const keep = out.filter(n => !done.includes(n))
        keep.push({ id: `fold:${id}`, kind: 'fold', parent: id, type: 'done', model: null, start: Math.max(...done.map(n => n.start)), members: done.map(n => n.agent!), phase: { ph: 'live', p: 1 } })
        byParent.set(id, keep.sort((x, y) => x.start - y.start))
        return
      }
    }
    byParent.set(id, out.sort((x, y) => x.start - y.start))
  }
  walk(MAIN)
  return byParent
}

const MAIN_NODE = (g: Graph): Node => {
  const m = g.agents.find(a => a.id === MAIN)!
  return { id: MAIN, kind: 'agent', parent: null, type: 'main', model: m.model, start: 0, agent: m, phase: { ph: 'live', p: 1 } }
}

/** The org chart at `now` for a pane `opts.columns` wide; `fits` false when even folded it is wider. */
export function chart(g: Graph, now: number, opts: ChartOpts, tween: Tween): Chart {
  const root = MAIN_NODE(g)
  let fold = new Set<string>()
  let tree = nodes(g, now, opts, fold)
  const kidsOf = (id: string) => tree.get(id) ?? []
  const width = (n: Node): number => {
    const k = kidsOf(n.id)
    return k.length ? Math.max(nodeW(n), k.reduce((s, c) => s + width(c), 0) + GAP * (k.length - 1)) : nodeW(n)
  }
  // Too wide: fold finished siblings, the parent with the most children first, until it fits or nothing folds.
  for (let guard = 0; width(root) > opts.columns - 2 && guard < 20; guard++) {
    const candidates = [...tree.entries()].filter(([id, k]) => !fold.has(id) && k.filter(n => n.kind === 'agent' && n.agent!.status === 'done').length >= 2)
    if (!candidates.length) break
    candidates.sort((x, y) => y[1].length - x[1].length)
    fold = new Set([...fold, candidates[0]![0]])
    tree = nodes(g, now, opts, fold)
  }
  const need = width(root)
  const fits = need <= opts.columns - 2

  // Targets: each parent centred over its children, everything relative to main.
  const target: Record<string, number> = {}
  const depth: Record<string, number> = {}
  const all: Node[] = []
  const place = (n: Node, x: number, d: number) => {
    all.push(n)
    const w = width(n)
    target[n.id] = x + w / 2
    depth[n.id] = d
    const k = kidsOf(n.id)
    const kw = k.reduce((s, c) => s + width(c), 0) + GAP * Math.max(0, k.length - 1)
    let cx = x + (w - kw) / 2
    for (const c of k) {
      place(c, cx, d + 1)
      cx += width(c) + GAP
    }
  }
  place(root, 0, 0)
  const m0 = num(target, MAIN)
  const goal: Record<string, number> = {}
  for (const n of all) goal[n.id] = Math.round(num(target, n.id) - m0)

  // One shared tween: a changed layout moves every box from where it is, together, so gaps change evenly.
  const sig = all.map(n => `${n.id}@${goal[n.id]}`).join(',')
  if (sig !== tween.sig) {
    const from: Record<string, number> = {}
    for (const n of all) from[n.id] = n.id in tween.disp ? num(tween.disp, n.id) : num(goal, n.id)
    tween.sig = sig
    tween.from = from
    tween.t0 = now
  }
  const tp = Math.min(1, (now - tween.t0) / TWEEN_MS)
  const ease = opts.motion ? 1 - Math.pow(1 - tp, 3) : 1
  const disp: Record<string, number> = {}
  for (const n of all) disp[n.id] = Math.round(num(tween.from, n.id) + (num(goal, n.id) - num(tween.from, n.id)) * ease)
  separate(all, depth, goal, disp)
  tween.disp = disp

  // The grid, main centred in the pane.
  const C = Math.floor(opts.columns / 2)
  const cx = (id: string) => C + num(disp, id)
  const maxD = Math.max(...Object.values(depth))
  const G: { ch: string; s: Omit<Seg, 'text'> }[][] = Array.from({ length: (maxD + 1) * (H + VG) - VG }, () =>
    Array.from({ length: opts.columns }, () => ({ ch: ' ', s: {} })),
  )
  const put = (x: number, y: number, text: string, s: Omit<Seg, 'text'> = {}) => {
    x = Math.round(x)
    const chars = [...text]
    const line = G[y]
    if (!line) return
    chars.forEach((ch, i) => {
      if (x + i >= 0 && x + i < opts.columns) line[x + i] = { ch, s }
    })
  }

  const squads: Chart['squads'] = []
  for (const n of all) {
    const y = num(depth, n.id) * (H + VG)
    if (n.kind === 'squad') {
      drawSquad(put, n, cx(n.id) - SW / 2, y, now, opts)
      squads.push({ id: n.id, label: `${n.type} ×${n.members!.length}` })
    } else if (n.kind === 'fold') drawFold(put, n, cx(n.id) - W / 2, y)
    else drawBox(put, n, cx(n.id) - W / 2, y, now, opts, g)
  }
  for (const n of all) wires(put, n, kidsOf(n.id), cx, depth, now, opts)

  const rows: Row[] = G.map(line => {
    const out: Row = []
    for (const c of line) {
      const last = out[out.length - 1]
      if (last && last.color === c.s.color && !!last.bold === !!c.s.bold && !!last.dim === !!c.s.dim) last.text += c.ch
      else out.push({ text: c.ch, ...c.s })
    }
    return trimRow(out)
  })

  const exp = opts.expanded ? all.find(n => n.id === opts.expanded && n.kind === 'squad') : undefined
  if (exp) rows.push(...expandedRows(exp, now, opts))
  const moving = tp < 1 || all.some(n => n.phase.ph !== 'live')
  return { rows, squads, fits, moving, width: need }
}

/** Spare columns the chart needs before the pane goes back to it from the tree. */
export const FIT_MARGIN = 4
/** How long the chart must keep fitting, with FIT_MARGIN to spare, before the pane goes back to it. */
export const FIT_HOLD_MS = 2000
/** Whether the pane falls back to the tree; `fitSince` is when the chart started fitting again. */
export type Fallback = { tree: boolean; fitSince: number | null }
export const noFallback = (): Fallback => ({ tree: false, fitSince: null })

/**
 * The tree fallback, with hysteresis so the pane never flickers between the two: the tree as soon as the chart
 * (`width` columns) overflows the pane, and the chart again only once it has fitted with FIT_MARGIN to spare for
 * FIT_HOLD_MS. A chart on the edge (a box entering, a fold, a scrollbar taking a column) stays a tree.
 */
export function fallback(prev: Fallback, width: number, columns: number, now: number): Fallback {
  const room = columns - 2 - width
  if (room < 0) return { tree: true, fitSince: null }
  if (!prev.tree) return prev
  if (room < FIT_MARGIN) return prev.fitSince === null ? prev : { tree: true, fitSince: null }
  const since = prev.fitSince ?? now
  return now - since >= FIT_HOLD_MS ? noFallback() : prev.fitSince === since ? prev : { tree: true, fitSince: since }
}

/** Keeps at least GAP columns between neighbours in each row, pushing both half the overlap, until none is left. */
export function separate(all: Node[] | { id: string; kind: string }[], depth: Record<string, number>, goal: Record<string, number>, disp: Record<string, number>) {
  const rows = new Map<number, { id: string; kind: string }[]>()
  for (const n of all) rows.set(num(depth, n.id), [...(rows.get(num(depth, n.id)) ?? []), n])
  for (const row of rows.values()) {
    row.sort((a, b) => num(goal, a.id) - num(goal, b.id))
    for (let pass = 0; pass < 12; pass++) {
      let moved = false
      for (let i = 1; i < row.length; i++) {
        const a = row[i - 1]!
        const b = row[i]!
        const need = (a.kind === 'squad' ? SW : W) / 2 + (b.kind === 'squad' ? SW : W) / 2 + GAP
        const d = need - (num(disp, b.id) - num(disp, a.id))
        if (d > 0) {
          const l = Math.floor(d / 2)
          disp[a.id] = num(disp, a.id) - l
          disp[b.id] = num(disp, b.id) + d - l
          moved = true
        }
      }
      if (!moved) break
    }
  }
}

function trimRow(r: Row): Row {
  for (let last = r.at(-1); last && last.text.trim() === '' && !last.color; last = r.at(-1)) r.pop()
  const last = r.at(-1)
  if (last) r[r.length - 1] = { ...last, text: last.text.replace(/\s+$/, '') || last.text }
  return r
}

type Put = (x: number, y: number, text: string, s?: Omit<Seg, 'text'>) => void

const statusLook = (st: string, model: string | null) =>
  st === 'running' ? { color: modelColor(model) } : st === 'waiting' ? { color: 'warning' } : st === 'failed' ? { color: 'error' } : { color: FAINT, dim: true }

function border(put: Put, x: number, y: number, w: number, s: Omit<Seg, 'text'>, dotted: boolean) {
  const hz = dotted ? '┄' : '─'
  const vt = dotted ? '┆' : '│'
  put(x, y, `╭${hz.repeat(w - 2)}╮`, s)
  for (let r = 1; r < H - 1; r++) {
    put(x, y + r, vt, s)
    put(x + w - 1, y + r, vt, s)
  }
  put(x, y + H - 1, `╰${hz.repeat(w - 2)}╯`, s)
}

function drawBox(put: Put, n: Node, x: number, y: number, now: number, opts: ChartOpts, g: Graph) {
  const a = n.agent!
  const isMain = a.id === MAIN
  const st = isMain ? (a.status === 'running' ? 'running' : 'done') : shownStatus(a, now)
  const elapsed = (a.ended ?? now) - a.started
  if (n.phase.ph === 'enter' && n.phase.p < 0.5) return // the connector is still growing
  if (n.phase.ph === 'chip') {
    // The chip stays inside its box's slot, so the gap to a neighbour never shrinks: the type gives way.
    const time = clock(elapsed)
    const txt = `${st === 'failed' ? '✗' : '✓'} ${pad(printable(a.type), Math.min(printable(a.type).length, W - 4 - 3 - time.length))} ${time}`
    put(x + W / 2 - Math.ceil(txt.length / 2) - 2, y + 2, `╶ ${txt} ╴`, st === 'failed' ? { color: 'error' } : { dim: true })
    return
  }
  const dim = n.phase.ph === 'dim'
  const look = dim ? { color: FAINT, dim: true } : statusLook(st, a.model)
  border(put, x, y, W, look, dim)
  if (n.phase.ph === 'enter' && n.phase.p < 0.8) return // border first, then content
  const inner = W - 4
  const glyph = st === 'running' ? spin(now, opts.motion) : st === 'waiting' ? '◐' : st === 'failed' ? '✗' : '✓'
  const tone = dim || st === 'done'
  put(x + 2, y + 1, glyph, dim ? { dim: true } : { ...statusLook(st, a.model), bold: true, dim: false })
  put(x + 4, y + 1, pad(isMain ? 'main' : printable(a.type), inner - 2), tone ? { bold: true, dim: true } : { color: modelColor(a.model), bold: true })
  let l2: string
  let l4: string
  let l4s: Omit<Seg, 'text'> = { dim: true }
  if (isMain) {
    const busy = g.agents.some(b => b.id !== MAIN && b.status === 'running')
    l2 = a.status === 'running' ? (busy ? 'waiting on agents' : 'thinking') : 'idle'
    l4 = g.turn ? `turn ${g.turn}` : ''
  } else {
    l2 = printable(a.desc)
    if (st === 'running') l4 = `⎿ ${a.step ?? 'thinking'}`
    else if (st === 'waiting') {
      l4 = '◐ needs you'
      l4s = { color: 'warning' }
    } else if (st === 'done') {
      l4 = `✓ ${a.steps} steps · ${clock(elapsed)}`
      l4s = { color: 'success', dim: dim }
    } else {
      l4 = `✗ ${a.why ?? 'failed'}`
      l4s = { color: 'error' }
    }
  }
  put(x + 2, y + 2, pad(l2, inner), tone ? { dim: true } : {})
  const model = modelLabel(a.model)
  put(x + 2, y + 3, pad(model, inner - 6), tone ? { dim: true } : { color: modelColor(a.model) })
  if (!isMain && st !== 'done') put(x + 2 + inner - 5, y + 3, clock(elapsed).padStart(5), { dim: true })
  put(x + 2, y + 4, pad(l4, inner), dim ? { dim: true } : l4s)
}

function drawFold(put: Put, n: Node, x: number, y: number) {
  border(put, x, y, W, { color: FAINT, dim: true }, false)
  put(x + 2, y + 2, pad(`✓ ${n.members!.length} done`, W - 4), { color: 'success', dim: true })
  put(x + 2, y + 3, pad(n.members!.map(m => printable(m.type)).join(', '), W - 4), { dim: true })
}

function drawSquad(put: Put, n: Node, x: number, y: number, now: number, opts: ChartOpts) {
  if (n.phase.ph === 'enter' && n.phase.p < 0.5) return
  const ms = n.members!
  const c = counts(ms, now)
  const live = c.running + c.waiting
  const dim = n.phase.ph === 'dim' || n.phase.ph === 'chip'
  const look = dim ? { color: FAINT, dim: true } : live ? { color: modelColor(n.model) } : c.failed ? { color: 'error' } : { color: FAINT, dim: true }
  border(put, x, y, SW, look, dim)
  put(x + 2, y + 1, live ? spin(now, opts.motion) : c.failed ? '✗' : '✓', dim ? { dim: true } : live ? { color: modelColor(n.model), bold: true } : c.failed ? { color: 'error', bold: true } : { color: 'success', bold: true })
  put(x + 4, y + 1, `${printable(n.type)} ×${ms.length}`.slice(0, SW - 18), dim ? { dim: true, bold: true } : { color: modelColor(n.model), bold: true })
  const model = modelLabel(n.model)
  put(x + SW - 2 - model.length, y + 1, model, dim ? { dim: true } : { color: modelColor(n.model) })
  // One cell per member, in launch order, so each keeps its spot; a gap every 5.
  let xx = x + 2
  ms.slice(0, 36).forEach((m, i) => {
    const st = shownStatus(m, now)
    const ch = st === 'running' ? spin(now, opts.motion, i) : st === 'waiting' ? '◐' : st === 'done' ? '✓' : '✗'
    put(xx, y + 2, ch, dim ? { dim: true } : st === 'running' ? { color: modelColor(m.model) } : st === 'waiting' ? { color: 'warning', bold: true } : st === 'done' ? { color: 'success' } : { color: 'error', bold: true })
    xx += (i + 1) % 5 === 0 ? 2 : 1
  })
  if (ms.length > 36) put(xx, y + 2, `+${ms.length - 36}`, { dim: true })
  let bx = x + 2
  for (const [text, s] of bar(c, ms.length, 18)) {
    put(bx, y + 3, text, dim ? { dim: true } : s)
    bx += text.length
  }
  const txt = `  ${c.done + c.failed}/${ms.length} · ${c.running} run`
  put(bx, y + 3, txt, { dim: true })
  bx += txt.length
  if (c.waiting) {
    put(bx, y + 3, ` ◐${c.waiting}`, { color: 'warning', bold: true })
    bx += 2 + String(c.waiting).length
  }
  if (c.failed) put(bx, y + 3, ` ✗${c.failed}`, { color: 'error', bold: true })
  put(x + 2, y + 4, '▸ x expand', { dim: true })
}

export function counts(ms: Agent[], now: number) {
  const c = { running: 0, waiting: 0, done: 0, failed: 0 }
  for (const m of ms) c[shownStatus(m, now)]++
  return c
}

/** A progress bar `w` wide: done and failed, running, waiting, then what is left. */
export function bar(c: ReturnType<typeof counts>, n: number, w: number): [string, Omit<Seg, 'text'>][] {
  const k = (x: number) => Math.round((x / Math.max(1, n)) * w)
  const d = k(c.done + c.failed)
  const r = k(c.running)
  const wt = k(c.waiting)
  return [
    ['█'.repeat(d), { color: 'success' }],
    ['▓'.repeat(r), { color: 'claude' }],
    ['▓'.repeat(wt), { color: 'warning' }],
    ['░'.repeat(Math.max(0, w - d - r - wt)), { color: FAINT, dim: true }],
  ].filter(([t]) => t !== '') as [string, Omit<Seg, 'text'>][]
}

const GLYPH: Record<string, string> = { UD: '│', LR: '─', DR: '╭', DL: '╮', UR: '╰', UL: '╯', DLR: '┬', ULR: '┴', UDLR: '┼', UDR: '├', UDL: '┤', U: '│', D: '│', L: '─', R: '─' }

/** The connectors from `n` down to its children: a stub, a bar, an arrow; a live child's path in its colour, with a dot. */
function wires(put: Put, n: Node, kids: Node[], cx: (id: string) => number, depth: Record<string, number>, now: number, opts: ChartOpts) {
  if (!kids.length) return
  const pc = Math.round(cx(n.id))
  const yb = num(depth, n.id) * (H + VG) + H
  const dirs = new Map<string, Set<string>>()
  const col = new Map<string, Omit<Seg, 'text'>>()
  const tips: [number, number][] = []
  const dots: [number, number, string][] = []
  const add = (x: number, y: number, d: string) => {
    const key = `${x},${y}`
    if (!dirs.has(key)) dirs.set(key, new Set())
    dirs.get(key)!.add(d)
  }
  add(pc, yb, 'U')
  for (const c of kids) {
    const cc = Math.round(cx(c.id))
    const path: [number, number][] = [[pc, yb], [pc, yb + 1]]
    for (let x = pc; x !== cc; x += Math.sign(cc - pc)) path.push([x + Math.sign(cc - pc), yb + 1])
    path.push([cc, yb + 2])
    const grow = c.phase.ph === 'enter' ? Math.min(1, c.phase.p / 0.5) : 1
    const shown = path.slice(0, Math.max(1, Math.ceil(path.length * grow)))
    const st = c.kind === 'agent' ? shownStatus(c.agent!, now) : c.kind === 'squad' ? (counts(c.members!, now).running ? 'running' : 'done') : 'done'
    const isLive = (st === 'running' || st === 'waiting') && c.phase.ph !== 'dim' && c.phase.ph !== 'chip'
    const color = st === 'waiting' ? 'warning' : modelColor(c.model)
    shown.forEach(([x, y], i) => {
      const nx = shown[i + 1]
      if (nx) {
        const d = nx[0] > x ? ['R', 'L'] : nx[0] < x ? ['L', 'R'] : ['D', 'U']
        add(x, y, d[0]!)
        add(nx[0], nx[1], d[1]!)
      }
      if (isLive || grow < 1) col.set(`${x},${y}`, { color })
    })
    if (shown.length === path.length) tips.push([cc, yb + 2])
    if (isLive && st === 'running' && grow === 1 && opts.motion) {
      const at = path[Math.floor(now / 125) % path.length]
      if (at) dots.push([at[0], at[1], color])
    }
  }
  for (const [key, s] of dirs) {
    const [x = 0, y = 0] = key.split(',').map(Number)
    const name = ['U', 'D', 'L', 'R'].filter(d => s.has(d)).join('')
    put(x, y, GLYPH[name] ?? '│', col.get(key) ?? { color: FAINT, dim: true })
  }
  for (const [x, y] of tips) put(x, y, '▼', col.get(`${x},${y}`) ?? { color: FAINT, dim: true })
  for (const [x, y, color] of dots) put(x, y, '•', { color, bold: true })
}

/** An expanded squad under the chart: one line per member, in as many columns as fit. */
function expandedRows(n: Node, now: number, opts: ChartOpts): Row[] {
  const CW = 40
  const cols = Math.max(1, Math.min(3, Math.floor(opts.columns / CW)))
  const ms = n.members!
  const per = Math.ceil(ms.length / cols)
  const rows: Row[] = [[], [{ text: `▾ ${printable(n.type)} ×${ms.length}`, color: modelColor(n.model), bold: true }, { text: '  x collapse', dim: true }], []]
  for (let r = 0; r < per; r++) {
    const row: Row = []
    for (let c = 0; c < cols; c++) {
      const m = ms[c * per + r]
      if (!m) continue
      const st = shownStatus(m, now)
      const glyph = st === 'running' ? spin(now, opts.motion, c * per + r) : st === 'waiting' ? '◐' : st === 'done' ? '✓' : '✗'
      const look = st === 'running' ? { color: modelColor(m.model) } : st === 'waiting' ? { color: 'warning' } : st === 'failed' ? { color: 'error' } : { color: 'success' }
      row.push({ text: glyph, ...look }, { text: ` ${pad(printable(m.desc), 22)} `, dim: st === 'done' }, { text: pad(st === 'waiting' ? 'needs you' : st, 10), ...look }, { text: pad(clock((m.ended ?? now) - m.started), 6), dim: true })
    }
    rows.push(row)
  }
  return rows
}

/** The agents to act on: waiting for a person, or failed this turn, wherever they are, never hidden in a squad. */
export function needsYou(g: Graph, now: number): Agent[] {
  return g.agents.filter(a => a.id !== MAIN && (shownStatus(a, now) === 'waiting' || (a.status === 'failed' && a.turn === g.turn)))
}

/** The line over the chart: counts for this turn, or what the last turn came to before any agent of this one. */
export function headerRow(g: Graph, now: number): Row {
  const mine = g.agents.filter(a => a.id !== MAIN && (a.turn === g.turn || a.status === 'running'))
  const c = counts(mine, now)
  if (!mine.length) {
    const lt = g.lastTurn
    return lt ? [{ text: 'last turn: ', dim: true }, { text: `✓ ${lt.done}`, color: 'success' }, { text: ' ' }, { text: `✗ ${lt.failed}`, color: 'error' }] : [{ text: 'no subagents yet', dim: true }]
  }
  const part = (n: number, label: string, color: string): Seg[] => [{ text: `${n} ${label}`, ...(n ? { color } : { dim: true }) }, { text: ' · ', dim: true }]
  const row = [...part(c.running, 'running', 'claude'), ...part(c.waiting, 'need you', 'warning'), ...part(c.done, 'done', 'success'), ...part(c.failed, 'failed', 'error')]
  row.pop()
  return row
}

/** The needs-you strip: at most two, then `+N more`; a quiet line when there is nothing. */
export function needsRow(g: Graph, now: number): Row {
  const need = needsYou(g, now)
  if (!need.length) return [{ text: 'needs you · nothing right now', dim: true }]
  const row: Row = [{ text: 'needs you  ', color: 'warning', bold: true }]
  need.slice(0, 2).forEach((a, i) => {
    const waiting = shownStatus(a, now) === 'waiting'
    if (i) row.push({ text: '    ' })
    row.push({ text: waiting ? '◐ ' : '✗ ', color: waiting ? 'warning' : 'error', bold: true }, { text: printable(a.desc || a.type) }, { text: ` · ${waiting ? a.step ?? 'permission' : a.why ?? 'failed'}`, dim: true })
  })
  if (need.length > 2) row.push({ text: `    +${need.length - 2} more`, color: 'warning' })
  return row
}

/** The fallback when even folded the chart does not fit: an indented tree, one or two lines per agent. */
export function tree(g: Graph, now: number, opts: ChartOpts): Row[] {
  const sched = schedule(g, opts.lingerMs)
  const rows: Row[] = []
  const walk = (id: string, prefix: string) => {
    const kids = g.agents.filter(a => a.parent === id && phaseOf(a, now, sched)).sort((x, y) => x.started - y.started)
    kids.forEach((a, i) => {
      const last = i === kids.length - 1
      const st = shownStatus(a, now)
      const glyph = st === 'running' ? spin(now, opts.motion) : st === 'waiting' ? '◐' : st === 'done' ? '✓' : '✗'
      const look = st === 'running' ? { color: modelColor(a.model) } : st === 'waiting' ? { color: 'warning' } : st === 'failed' ? { color: 'error' } : { color: 'success' }
      rows.push([
        { text: `${prefix}${last ? '└─ ' : '├─ '}`, color: FAINT, dim: true },
        { text: glyph, ...look, bold: true },
        { text: ` ${pad(printable(a.type), 18)}`, ...(st === 'done' ? { dim: true } : { color: modelColor(a.model), bold: true }) },
        { text: pad(printable(a.desc), 26), dim: st === 'done' },
        { text: pad(modelLabel(a.model), 11), color: modelColor(a.model) },
        { text: st === 'done' || st === 'failed' ? `${st} ${clock((a.ended ?? now) - a.started)}${a.why ? ` · ${a.why}` : ''}` : clock(now - a.started), ...(st === 'failed' ? { color: 'error' } : { dim: true }) },
      ])
      if (st === 'running' || st === 'waiting') rows.push([{ text: `${prefix}${last ? '   ' : '│  '}   ⎿ `, color: FAINT, dim: true }, { text: st === 'waiting' ? 'needs you' : a.step ?? 'thinking', ...(st === 'waiting' ? { color: 'warning' } : { dim: true }) }])
      walk(a.id, prefix + (last ? '   ' : '│  '))
    })
  }
  const m = g.agents.find(a => a.id === MAIN)!
  rows.push([{ text: m.status === 'running' ? spin(now, opts.motion) : '◆', color: 'claude', bold: true }, { text: ' main', color: 'claude', bold: true }, { text: m.model ? `  ${modelLabel(m.model)}` : '', dim: true }])
  walk(MAIN, '')
  return rows
}

export { ENTER_MS }
