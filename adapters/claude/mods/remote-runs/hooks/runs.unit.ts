import assert from 'node:assert/strict'
import { test } from 'node:test'

import {
  applyLiveness,
  commonDir,
  endedRuns,
  formatRow,
  gitDirs,
  livePids,
  notifyText,
  parseRun,
  pickRows,
  type Run,
  type RunFiles,
  signature,
  statusText,
} from './runs.ts'

const LSTART = 'Mon Nov  2 10:00:00 2026'

// A run folder's files as the mod reads them; `json` becomes run.json (null: no run.json).
function files(id: string, json: Record<string, unknown> | null, progress: string | null = null, changed = 0): RunFiles {
  return { id, folder: `/repo/.git/remote-agent/${id}`, runJson: json && JSON.stringify(json), progress, changed }
}

function runJson(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    run: 'agent-1', name: 'TT-01-list', worktree: '/repo', started: 1000, pid: 42, pid_start: LSTART,
    state: 'running', exit_code: null, ended: null, claim_s: 3, send_s: 5, first_event_s: 12, ...over,
  }
}

function run(id: string, state: Run['state'], started: number, over: Partial<Run> = {}): Run {
  return { ...parseRun(files(id, runJson({ started }))), state, ...over }
}

const ORDER = ['done', 'failed', 'running', 'young', 'stopped', 'unknown']

test('commonDir takes the absolute path git printed', () => {
  assert.equal(commonDir(0, '/repo/.git\n'), '/repo/.git')
  assert.equal(commonDir(0, '/repo/.git/\n'), '/repo/.git')
})

test('commonDir is null when git fails or prints no absolute path', () => {
  assert.equal(commonDir(128, 'fatal: not a git repository\n'), null)
  assert.equal(commonDir(1, '/repo/.git\n'), null)
  assert.equal(commonDir(0, ''), null)
  assert.equal(commonDir(0, '.git\n'), null)
})

test('gitDirs lists the common dir, then each worktree git dir in name order', () => {
  assert.deepEqual(gitDirs('/repo/.git', ['b', 'a']), ['/repo/.git', '/repo/.git/worktrees/a', '/repo/.git/worktrees/b'])
  assert.deepEqual(gitDirs('/repo/.git', []), ['/repo/.git'])
})

test('parseRun reads every column from run.json and the last line of progress.log', () => {
  const r = parseRun(files('agent-1', runJson(), 'claimed in 3s\r\nfirst agent event after 12s\n\n', 1100))
  assert.equal(r.name, 'TT-01-list')
  assert.equal(r.state, 'running')
  assert.equal(r.started, 1000)
  assert.equal(r.step, 'first agent event after 12s')
  assert.deepEqual([r.claim_s, r.send_s, r.first_event_s], [3, 5, 12])
  assert.deepEqual([r.pid, r.pid_start, r.worktree], [42, LSTART, '/repo'])
  assert.equal(r.transcript, '/repo/.git/remote-agent/agent-1/transcript.jsonl')
  assert.equal(r.changed, 1100)
})

test('parseRun shows a folder with no run.json, or an unreadable one, as unknown, dated by its id', () => {
  for (const text of [null, '{"state": "runn', '[1]', '"x"']) {
    const r = parseRun({ id: 'agent-20261102-100000-7', folder: '/f', runJson: text, progress: null, changed: 0 })
    assert.equal(r.state, 'unknown')
    assert.equal(r.name, null)
    assert.equal(r.step, null)
    assert.equal(r.started, new Date(2026, 10, 2, 10, 0, 0).getTime() / 1000)
  }
  assert.equal(parseRun(files('no-date', null)).started, null)
})

test('parseRun drops fields of the wrong type and an unknown state', () => {
  const r = parseRun(files('a', runJson({ name: 7, claim_s: 'x', pid: '42', state: 'weird' }), ''))
  assert.deepEqual([r.name, r.claim_s, r.pid, r.state, r.step], [null, null, null, 'unknown', null])
})

test('livePids lists the pids of starting and running runs only', () => {
  const rows = [run('a', 'running', 1, { pid: 1 }), run('b', 'starting', 1, { pid: 2 }), run('c', 'done', 1, { pid: 3 })]
  assert.deepEqual(livePids(rows), [1, 2])
})

test('applyLiveness stops a run whose pid is dead or started at another time, and keeps a live one', () => {
  const rows = [
    run('alive', 'running', 1, { pid: 1 }),
    run('dead', 'running', 1, { pid: 2 }),
    run('reused', 'starting', 1, { pid: 3 }),
    run('done', 'done', 1, { pid: 4 }),
  ]
  const ps = `    1 Mon Nov  2 10:00:00 2026\n    3 Tue Nov  3 09:00:00 2026\n`
  assert.deepEqual(applyLiveness(rows, ps).map(r => r.state), ['running', 'stopped', 'stopped', 'done'])
})

test('applyLiveness changes nothing when ps could not run', () => {
  const rows = [run('a', 'running', 1, { pid: 2 })]
  assert.deepEqual(applyLiveness(rows, null), rows)
})

test('pickRows keeps every unfinished run, then the newest finished, 20 at most, newest first', () => {
  const finished = Array.from({ length: 30 }, (_, i) => run(`f${i}`, i % 2 ? 'done' : 'failed', 100 + i))
  const open = [run('old-running', 'running', 1), run('stopped', 'stopped', 50), run('starting', 'starting', 200)]
  const rows = pickRows([...finished, ...open], 1000)
  assert.equal(rows.length, 20)
  assert.deepEqual(rows.slice(0, 3).map(r => r.id), ['starting', 'stopped', 'old-running'])
  assert.deepEqual(rows.slice(3).map(r => r.id), Array.from({ length: 17 }, (_, i) => `f${29 - i}`))
})

test('pickRows counts an unknown run as finished', () => {
  const rows = pickRows([run('u', 'unknown', 500), run('r', 'running', 1)], 1000)
  assert.deepEqual(rows.map(r => r.id), ['r', 'u'])
})

test('pickRows shows elapsed exactly for an ended run and to the minute for one going on', () => {
  const rows = pickRows([
    run('done', 'done', 1000, { ended: 1083 }),
    run('failed', 'failed', 900, { ended: 905 }),
    run('running', 'running', 1000 - 3725),
    run('young', 'running', 990),
    run('stopped', 'stopped', 800, { changed: 830 }),
    run('unknown', 'unknown', 700),
  ], 1000)
  rows.sort((a, b) => ORDER.indexOf(a.id) - ORDER.indexOf(b.id))
  assert.deepEqual(rows.map(r => r.elapsed), ['1m23s', '5s', '1h02m', '<1m', '30s', ''])
})

test('signature is the same for rows that draw the same, and differs on a new step or state', () => {
  const a = pickRows([run('a', 'running', 990, { step: 'x' })], 1000)
  assert.equal(signature(a), signature(pickRows([run('a', 'running', 990, { step: 'x' })], 1010)))
  assert.notEqual(signature(a), signature(pickRows([run('a', 'running', 990, { step: 'y' })], 1000)))
  assert.notEqual(signature(a), signature(pickRows([run('a', 'done', 990, { step: 'x', ended: 995 })], 1000)))
})

test('formatRow names the run, else its id, with its state, elapsed time, timings and last step', () => {
  const [r] = pickRows([run('agent-1', 'running', 990, { step: 'first agent event after 12s' })], 1000)
  assert.deepEqual(formatRow(r), {
    label: 'TT-01-list',
    status: 'running <1m',
    detail: 'claim 3s · send 5s · first 12s · first agent event after 12s',
  })
  const [bare] = pickRows([parseRun(files('agent-2', null))], 1000)
  assert.deepEqual(formatRow(bare), { label: 'agent-2', status: 'unknown', detail: '' })
})

test('statusText counts running and starting runs, and failed or stopped ones, leaving out a zero part', () => {
  const now = 10_000
  const running = [run('r', 'running', 1), run('s', 'starting', 1)]
  const failed = [run('f', 'failed', 1, { ended: now - 60 })]
  assert.equal(statusText([], now), undefined)
  assert.equal(statusText(running, now), 'remote: 2 running')
  assert.equal(statusText(failed, now), 'remote: 1 failed')
  assert.equal(statusText([...running, ...failed], now), 'remote: 2 running, 1 failed')
})

test('statusText counts a stopped run as failed, and ignores done and unknown runs', () => {
  const now = 10_000
  const rows = [run('st', 'stopped', 1, { changed: now - 5 }), run('d', 'done', 1, { ended: now }), run('u', 'unknown', 1)]
  assert.equal(statusText(rows, now), 'remote: 1 failed')
})

test('statusText counts failed and stopped runs only for 60 minutes after they ended', () => {
  const now = 10_000
  const at = (ago: number) => [
    run('f', 'failed', 1, { ended: now - ago }),
    run('st', 'stopped', 1, { changed: now - ago }),
  ]
  assert.equal(statusText(at(3600), now), 'remote: 2 failed')
  assert.equal(statusText(at(3601), now), undefined)
  assert.equal(statusText([run('f', 'failed', 1, { ended: null, changed: now - 10 })], now), 'remote: 1 failed')
})

test('endedRuns lists done, failed and stopped runs not yet notified', () => {
  const rows = [
    run('d', 'done', 1), run('f', 'failed', 1), run('st', 'stopped', 1),
    run('r', 'running', 1), run('s', 'starting', 1), run('u', 'unknown', 1), run('old', 'done', 1),
  ]
  const seen = new Set([rows[6].folder])
  assert.deepEqual(endedRuns(rows, seen).map(r => r.id), ['d', 'f', 'st'])
})

test('notifyText names the run, else its id, and how it ended', () => {
  assert.equal(notifyText(run('a', 'done', 1)), 'TT-01-list done')
  assert.equal(notifyText(run('a', 'failed', 1, { exit_code: 2 })), 'TT-01-list failed (exit 2)')
  assert.equal(notifyText(run('a', 'failed', 1, { exit_code: null })), 'TT-01-list failed')
  assert.equal(notifyText(run('agent-9', 'stopped', 1, { name: null })), 'agent-9 stopped')
})
