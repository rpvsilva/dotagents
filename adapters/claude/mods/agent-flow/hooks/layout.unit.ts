import assert from 'node:assert/strict'
import { test } from 'node:test'

import { emptyGraph, type FlowEvent, type Graph, MAIN, reduce, WAIT_MS } from './flow.ts'
import { chart, type ChartOpts, fallback, FIT_HOLD_MS, FIT_MARGIN, GAP, noFallback, headerRow, needsRow, newTween, type Row, SW, tree, TWEEN_MS, W } from './layout.ts'

const T = 1_800_000_000_000
const OPTS: ChartOpts = { columns: 160, lingerMs: 5000, squadMin: 4, motion: true, expanded: null }
const text = (r: Row) => r.map(s => s.text).join('')
const lines = (rows: Row[]) => rows.map(text)
const spawn = (id: string, at: number, type = 'Explore', parent = MAIN, model = 'claude-sonnet-5-5'): FlowEvent => ({
  kind: 'spawn', at, id, parent, type, desc: `task ${id}`, model, parentModel: 'claude-opus-5-5', toolUseId: `tu-${id}`,
})
const end = (id: string, at: number, reason = 'answer'): FlowEvent => ({ kind: 'turnEnd', at, agent: id, reason, outputTokens: null })
const graphAt = (evs: FlowEvent[], now: number): Graph => evs.filter(e => e.at <= now).reduce(reduce, emptyGraph())
const settled = (evs: FlowEvent[], now: number, opts: Partial<ChartOpts> = {}) => {
  // Draws every frame up to `now`, as the pane does, so the tween has settled.
  const tw = newTween()
  let c = chart(graphAt(evs, T), T, { ...OPTS, ...opts }, tw)
  for (let t = T; t <= now; t += 125) c = chart(graphAt(evs, t), t, { ...OPTS, ...opts }, tw)
  return c
}

test('main sits centred at the top; an agent hangs under it in a box, joined by a connector', () => {
  const evs = [{ kind: 'turnStart', at: T, agent: MAIN } as FlowEvent, spawn('a1', T + 100)]
  const c = settled(evs, T + 2000)
  const ls = lines(c.rows)
  const mainRow = ls.findIndex(l => l.includes('main'))
  assert.ok(mainRow >= 0)
  const mainCol = ls[mainRow]!.indexOf('main')
  assert.ok(Math.abs(mainCol - OPTS.columns / 2) < W, `main at column ${mainCol}`)
  assert.ok(ls.some(l => l.includes('Explore')))
  assert.ok(ls.some(l => l.includes('▼')))
  assert.ok(ls.some(l => l.includes('sonnet 5.5')))
  assert.equal(c.fits, true)
})

test('four siblings of one type fold into one squad box with a cell each; three stay boxes', () => {
  const three = [0, 1, 2].map(i => spawn(`e${i}`, T + i, 'epic-implementer'))
  const four = [...three, spawn('e3', T + 3, 'epic-implementer')]
  assert.equal(settled(three, T + 2000).squads.length, 0)
  const c = settled(four, T + 2000)
  assert.deepEqual(c.squads.map(s => s.label), ['epic-implementer ×4'])
  assert.ok(lines(c.rows).some(l => l.includes('epic-implementer ×4')))
})

test('a squad box stays one width from 5 agents to 24', () => {
  const widthOf = (n: number) => {
    const c = settled(Array.from({ length: n }, (_, i) => spawn(`e${i}`, T + i, 'epic-implementer')), T + 2000)
    const top = lines(c.rows).find(l => l.includes('epic-implementer'))!
    return top.lastIndexOf('│') - top.indexOf('│') + 1
  }
  assert.equal(widthOf(5), SW)
  assert.equal(widthOf(24), SW)
})

test('a row too wide folds its finished siblings into one box; when nothing can fold it does not fit', () => {
  const done = Array.from({ length: 6 }, (_, i) => [spawn(`d${i}`, T + i, `type${i}`), end(`d${i}`, T + 1000 + i)]).flat()
  const c = settled(done, T + 2500, { columns: 100, lingerMs: 60_000 })
  assert.equal(c.fits, true)
  assert.ok(lines(c.rows).some(l => l.includes('✓ 6 done')))
  const running = Array.from({ length: 6 }, (_, i) => spawn(`r${i}`, T + i, `type${i}`))
  assert.equal(settled(running, T + 2500, { columns: 100 }).fits, false)
})

test('the tree view lists every agent under main with its status, model and current step', () => {
  const evs = [spawn('a1', T), spawn('a2', T + 1, 'pr-reviewer', 'a1', 'claude-opus-5-5'), { kind: 'tool', at: T + 2, agent: 'a2', tool: 'Bash', summary: 'Bash gh pr diff' } as FlowEvent]
  const ls = lines(tree(graphAt(evs, T + 10), T + 10, OPTS))
  assert.match(ls[0]!, /main/)
  assert.ok(ls.some(l => /└─ .*Explore/.test(l)))
  assert.ok(ls.some(l => /└─ .*pr-reviewer .*opus 5\.5/.test(l)))
  assert.ok(ls.some(l => l.includes('⎿ Bash gh pr diff')))
})

test('the strips: counts for this turn, the agents that need you, the last turn once a new prompt clears it', () => {
  const evs: FlowEvent[] = [
    { kind: 'turnStart', at: T, agent: MAIN }, spawn('ok', T), spawn('wait', T), spawn('bad', T),
    { kind: 'tool', at: T, agent: 'wait', tool: 'Bash', summary: 'Bash rm -rf build' }, { kind: 'check', at: T, agent: 'wait', toolUseId: 't' },
    end('ok', T + 10), end('bad', T + 10, 'error'),
  ]
  const g = graphAt(evs, T + WAIT_MS)
  assert.equal(text(headerRow(g, T + WAIT_MS)), '0 running · 1 need you · 1 done · 1 failed')
  assert.equal(text(needsRow(g, T + WAIT_MS)), 'needs you  ◐ task wait · Bash rm -rf build    ✗ task bad · error')
  const next = reduce(reduce(g, { kind: 'checkEnd', at: T + 2000, agent: 'wait', toolUseId: 't' }), end('wait', T + 2001))
  const cleared = reduce(next, { kind: 'turnStart', at: T + 3000, agent: MAIN })
  assert.equal(text(headerRow(cleared, T + 3000)), 'last turn: ✓ 2 ✗ 1')
  assert.equal(text(needsRow(cleared, T + 3000)), 'needs you · nothing right now')
})

test('an expanded squad lists each member under the chart', () => {
  const evs = Array.from({ length: 6 }, (_, i) => spawn(`e${i}`, T + i, 'epic-implementer'))
  const c0 = settled(evs, T + 2000)
  const c = settled(evs, T + 2000, { expanded: c0.squads[0]!.id })
  const ls = lines(c.rows)
  assert.ok(ls.some(l => l.includes('▾ epic-implementer ×6')))
  assert.equal(ls.filter(l => /task e\d/.test(l)).length > 0, true)
  assert.ok(['task e0', 'task e5'].every(t => ls.some(l => l.includes(t))))
})

test('motion off draws the same chart still: no spinner frames, no dots', () => {
  const evs = [spawn('a1', T)]
  const ls = lines(settled(evs, T + 2000, { motion: false }).rows).join('\n')
  assert.ok(!/[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏•]/.test(ls))
  assert.ok(ls.includes('●'))
})

// The frame scan: two sessions played frame by frame (8 per second), boxes entering, gliding, lingering and leaving.
// In no frame may two boxes touch or overlap: a right corner then 0 or 1 space then a left corner, a border running
// into another box, or a chip against a border. And boxes never swap order.
const BAD = /[╮╯]\s?[╭╰]|[─┄][╭╰]|[╮╯][─┄]|╴\s?[│┆╭╰]|[│┆╮╯]\s?╶/
function scan(evs: FlowEvent[], until: number, columns: number) {
  const tw = newTween()
  const bad: string[] = []
  for (let t = T; t <= until; t += 125) {
    const c = chart(graphAt(evs, t), t, { ...OPTS, columns }, tw)
    for (const l of lines(c.rows)) if (BAD.test(l) && bad.length < 3) bad.push(`t=${t - T}: ${l.trim()}`)
  }
  return bad
}

test('frame scan, five agents and a fork: no box ever touches another', () => {
  const evs: FlowEvent[] = [
    { kind: 'turnStart', at: T, agent: MAIN },
    spawn('A', T + 1000), spawn('B', T + 2500, 'pr-reviewer', MAIN, 'claude-opus-5-5'), spawn('C', T + 4000, 'epic-implementer', MAIN, 'claude-haiku-5-5'),
    spawn('D', T + 6000, 'fork', 'C', 'claude-haiku-5-5'), end('A', T + 9000), spawn('E', T + 10_000, 'general-purpose', MAIN, 'claude-fable-5-2'),
    end('D', T + 11_500), end('B', T + 14_000), end('E', T + 15_000, 'error'), end('C', T + 16_500), end(MAIN, T + 17_500),
    { kind: 'turnStart', at: T + 18_000, agent: MAIN },
  ]
  assert.deepEqual(scan(evs, T + 21_000, 160), [])
})

test('frame scan, a 24-agent fan-out beside a reviewer: no box ever touches another', () => {
  const evs: FlowEvent[] = [{ kind: 'turnStart', at: T, agent: MAIN }, spawn('X', T + 300), spawn('R', T + 1500, 'pr-reviewer', MAIN, 'claude-opus-5-5'), end('X', T + 5000)]
  for (let i = 0; i < 24; i++) {
    evs.push(spawn(`T${i}`, T + 600 + i * 300, 'epic-implementer', MAIN, 'claude-haiku-5-5'))
    evs.push(end(`T${i}`, T + 600 + i * 300 + 7000 + ((i * 7) % 9) * 1000, i === 18 ? 'error' : 'answer'))
  }
  evs.push(end('R', T + 20_000), end(MAIN, T + 23_500), { kind: 'turnStart', at: T + 24_000, agent: MAIN })
  assert.deepEqual(scan(evs, T + 27_000, 160), [])
})

test("a chip never leaves its box's slot, however long the agent type", () => {
  const evs = [spawn('a', T, 'a-very-long-agent-type-name'), end('a', T + 100)]
  const c = settled(evs, T + 100 + 5000 + 800)
  const chip = lines(c.rows).find(l => l.includes('╶'))!
  assert.ok(chip, 'a chip is drawn')
  const w = [...chip.trim()].length
  assert.ok(w <= W, `chip is ${w} columns: ${chip.trim()}`)
})

test('the glide: boxes move together over TWEEN_MS and never come closer than GAP columns', () => {
  const evs: FlowEvent[] = [spawn('a', T), spawn('b', T + 1), spawn('c', T + 2), end('a', T + 100)]
  const tw = newTween()
  const opts = { ...OPTS, lingerMs: 1000 }
  // a leaves at T+1100 and is gone at T+2100; b and c then glide to close the gap.
  for (let t = T; t <= T + 2100 + TWEEN_MS + 250; t += 25) {
    const c = chart(graphAt(evs, t), t, opts, tw)
    const top = lines(c.rows).find(l => /[╭┄]/.test(l) && !l.includes('main') && (l.match(/╭/g) ?? []).length >= 2)
    if (!top) continue
    for (const m of top.matchAll(/[╮]( *)[╭]/g)) assert.ok(m[1]!.length >= GAP, `t=${t - T}: gap ${m[1]!.length} in ${top.trim()}`)
  }
})

test('the tree fallback never flickers: tree at once on overflow, chart only after it fits with room for a while', () => {
  const cols = 100
  let f = noFallback()
  f = fallback(f, 98, cols, T) // fits exactly
  assert.equal(f.tree, false)
  f = fallback(f, 99, cols, T + 125) // one column over
  assert.equal(f.tree, true)
  // On the edge, back and forth: stays a tree.
  for (let t = T + 250; t < T + 10_000; t += 125) {
    f = fallback(f, t % 250 ? 98 : 99, cols, t)
    assert.equal(f.tree, true, `t=${t - T}`)
  }
  // Room to spare, but not for long enough: still a tree; then the chart.
  const roomy = cols - 2 - FIT_MARGIN
  f = fallback(f, roomy, cols, T + 20_000)
  f = fallback(f, roomy, cols, T + 20_000 + FIT_HOLD_MS - 1)
  assert.equal(f.tree, true)
  f = fallback(f, roomy, cols, T + 20_000 + FIT_HOLD_MS)
  assert.equal(f.tree, false)
})
