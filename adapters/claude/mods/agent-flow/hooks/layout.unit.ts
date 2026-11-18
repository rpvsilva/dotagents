import assert from 'node:assert/strict'
import { test } from 'node:test'

import { emptyGraph, type FlowEvent, type Graph, MAIN, reduce, WAIT_MS } from './flow.ts'
import { chart, type ChartOpts, fallback, FIT_HOLD_MS, FIT_MARGIN, GAP, noFallback, headerRow, needsRow, newTween, type Row, SW, TWEEN_MS, W } from './layout.ts'

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

test('a squad that has gone does not count again: a later lone agent of its type is a box, a later four a new squad ×4', () => {
  const first = [0, 1, 2, 3].flatMap(i => [spawn(`e${i}`, T + i, 'epic-implementer'), end(`e${i}`, T + 1000 + i)])
  const gone = T + 1003 + OPTS.lingerMs + 1000 + 1
  const solo = [...first, spawn('s0', gone + 100, 'epic-implementer')]
  const c1 = settled(solo, gone + 2000)
  assert.equal(c1.squads.length, 0)
  assert.ok(lines(c1.rows).some(l => l.includes('epic-implementer') && !l.includes('×')))
  const four = [...first, ...[0, 1, 2, 3].map(i => spawn(`n${i}`, gone + 100 + i, 'epic-implementer'))]
  const c2 = settled(four, gone + 2000)
  assert.deepEqual(c2.squads.map(s => s.label), ['epic-implementer ×4'])
  assert.notEqual(c2.squads[0]!.id, settled(first, T + 1500).squads[0]!.id)
})

test('an agent started while its type\'s squad is still shown joins it', () => {
  const evs = [...[0, 1, 2, 3].map(i => spawn(`e${i}`, T + i, 'epic-implementer')), end('e0', T + 1000), spawn('e4', T + 1500, 'epic-implementer')]
  assert.deepEqual(settled(evs, T + 3000).squads.map(s => s.label), ['epic-implementer ×5'])
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

test('the narrow fallback never flickers: the list at once on overflow, the chart only after it fits with room for a while', () => {
  const cols = 100
  let f = noFallback()
  f = fallback(f, 98, cols, T) // fits exactly
  assert.equal(f.narrow, false)
  f = fallback(f, 99, cols, T + 125) // one column over
  assert.equal(f.narrow, true)
  // On the edge, back and forth: stays the list.
  for (let t = T + 250; t < T + 10_000; t += 125) {
    f = fallback(f, t % 250 ? 98 : 99, cols, t)
    assert.equal(f.narrow, true, `t=${t - T}`)
  }
  // Room to spare, but not for long enough: still the list; then the chart.
  const roomy = cols - 2 - FIT_MARGIN
  f = fallback(f, roomy, cols, T + 20_000)
  f = fallback(f, roomy, cols, T + 20_000 + FIT_HOLD_MS - 1)
  assert.equal(f.narrow, true)
  f = fallback(f, roomy, cols, T + 20_000 + FIT_HOLD_MS)
  assert.equal(f.narrow, false)
})

test("a box carries its context gauge in its bottom border, coloured by how full; a squad its average and fullest", () => {
  const ctx = (id: string, used: number, window = 200_000): FlowEvent => ({ kind: 'ctx', at: T + 50, agent: id, used, window })
  const c = settled([spawn('a', T), ctx('a', 170_000)], T + 2000)
  const bottom = c.rows.find(r => text(r).includes('╰─ ▰'))!
  assert.ok(bottom, 'a gauge in a bottom border')
  assert.match(text(bottom), /╰─ ▰▰▰▰▰▱ 170k\/200k ─+╯/)
  assert.equal(bottom.find(sg => sg.text.includes('▰'))!.color, 'error')
  const sq = Array.from({ length: 4 }, (_, i) => spawn(`e${i}`, T + i, 'epic-implementer'))
  const reads = [ctx('e0', 20_000), ctx('e1', 40_000), ctx('e2', 60_000), ctx('e3', 120_000)]
  assert.ok(lines(settled([...sq, ...reads], T + 2000).rows).some(l => /ctx avg 30% · max ▰▰▰▰▱▱ 60%/.test(l)))
  // No reading yet: a plain border.
  assert.ok(!lines(settled([spawn('b', T)], T + 2000).rows).some(l => l.includes('▱')))
})
