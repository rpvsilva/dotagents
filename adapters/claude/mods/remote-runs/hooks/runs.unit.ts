import assert from 'node:assert/strict'
import { test } from 'node:test'

import {
  applyLiveness,
  commonDir,
  endedRuns,
  formatRow,
  gitDirs,
  livePids,
  markNotified,
  metaLine,
  newLiveRuns,
  openPaneSetting,
  notifyText,
  parseRun,
  OLD_S,
  pickRows,
  printable,
  type Run,
  type RunFiles,
  signature,
  statusText,
  whenLine,
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

// Control bytes a sandbox could put in its files: ESC, a whole OSC 52 clipboard write ended by BEL, an OSC 8 link,
// a cursor move and C1 controls (CSI, OSC, NEL).
const ESC = '\u001b'
const BEL = '\u0007'
const HOSTILE = `a${ESC}]52;c;aGk=${BEL}b${ESC}]8;;http://x${ESC}\\c${ESC}[2Ad\u009b31m\u009d0;t\u0085e\u0000\u007f\tf`

test('printable drops every C0 and C1 control character but tab', () => {
  assert.equal(printable(HOSTILE), 'a]52;c;aGk=b]8;;http://x\\c[2Ad31m0;te\tf')
  assert.equal(printable('plain · text é'), 'plain · text é')
})

test('parseRun strips control characters from the name, the worktree and the last progress line', () => {
  const r = parseRun(files('agent-1', runJson({ name: HOSTILE, worktree: HOSTILE }), `first\n${HOSTILE}\n`))
  const clean = printable(HOSTILE)
  assert.deepEqual([r.name, r.worktree, r.step], [clean, clean, clean])
  for (const field of [r.name, r.worktree, r.step]) assert.doesNotMatch(field ?? '', /[\u0000-\u0008\u000a-\u001f\u007f-\u009f]/)
})

test('livePids lists the pids of starting and running runs only', () => {
  const rows = [run('a', 'running', 1, { pid: 1 }), run('b', 'starting', 1, { pid: 2 }), run('c', 'done', 1, { pid: 3 })]
  assert.deepEqual(livePids(rows), [1, 2])
})

test('livePids lists a shared pid once and skips a live run with no pid', () => {
  const rows = [run('a', 'running', 1, { pid: 5 }), run('b', 'starting', 1, { pid: 5 }), run('c', 'running', 1, { pid: null })]
  assert.deepEqual(livePids(rows), [5])
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

test('applyLiveness keeps a live run with no recorded start or no pid, whatever the spacing of ps', () => {
  const rows = [
    run('no-start', 'running', 1, { pid: 1, pid_start: null }),
    run('no-pid', 'running', 1, { pid: null }),
    run('spaced', 'running', 1, { pid: 3, pid_start: ' Mon Nov 2  10:00:00 2026 ' }),
  ]
  const ps = `1 Tue Nov  3 09:00:00 2026\n    3 Mon  Nov  2 10:00:00   2026  \n`
  assert.deepEqual(applyLiveness(rows, ps).map(r => r.state), ['running', 'running', 'running'])
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

test('pickRows shows no elapsed time for an ended run with no end or a run with no start', () => {
  const rows = pickRows([
    run('done', 'done', 1000, { ended: null }),
    run('failed', 'failed', 900, { ended: null }),
    run('running', 'running', 900, { started: null }),
  ], 1000)
  assert.deepEqual(rows.map(r => [r.id, r.elapsed]), [['running', ''], ['done', ''], ['failed', '']])
})

test('pickRows orders runs started at the same second by id, newest id first', () => {
  const rows = pickRows([run('agent-a', 'done', 500), run('agent-c', 'done', 500), run('agent-b', 'done', 500)], 1000)
  assert.deepEqual(rows.map(r => r.id), ['agent-c', 'agent-b', 'agent-a'])
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
    detail: 'first agent event after 12s · claim 3s · send 5s · first 12s',
    glyph: '●',
    tone: 'warning',
  })
  const [bare] = pickRows([parseRun(files('agent-2', null))], 1000)
  assert.deepEqual(formatRow(bare), { label: 'agent-2', status: 'unknown', detail: '', glyph: '?', tone: 'inactive' })
})

test('formatRow gives each state its glyph and theme color', () => {
  const states: Run['state'][] = ['starting', 'running', 'done', 'failed', 'stopped', 'unknown']
  const got = states.map(s => {
    const { glyph, tone } = formatRow({ ...run('a', s, 990), elapsed: '', ago: '', isOld: false })
    return [s, glyph, tone]
  })
  assert.deepEqual(got, [
    ['starting', '○', 'warning'],
    ['running', '●', 'warning'],
    ['done', '✓', 'success'],
    ['failed', '✗', 'error'],
    ['stopped', '■', 'warning'],
    ['unknown', '?', 'inactive'],
  ])
})

test('formatRow adds how long ago a finished run ended to its status', () => {
  const [r] = pickRows([run('a', 'done', 1000, { ended: 1034 })], 1034 + 180)
  assert.equal(formatRow(r).status, 'done 34s · 3m ago')
})

test('pickRows says how long ago a finished run ended, coarsely, and nothing for one going on', () => {
  const now = 100_000
  const rows = pickRows([
    run('live', 'running', now - 10),
    run('just', 'done', now - 100, { ended: now - 20 }),
    run('mins', 'failed', now - 900, { ended: now - 600 }),
    run('hours', 'done', now - 9000, { ended: now - 7300 }),
    run('days', 'done', now - 300_000, { ended: now - 200_000 }),
    run('stopped', 'stopped', now - 500, { changed: now - 400 }),
    run('noend', 'done', now - 500, { ended: null, changed: now - 120 }),
  ], now)
  assert.deepEqual(Object.fromEntries(rows.map(r => [r.id, r.ago])), {
    live: '', just: '<1m ago', mins: '10m ago', hours: '2h ago', days: '2d ago', stopped: '6m ago', noend: '2m ago',
  })
})

test('pickRows marks old a finished run that ended more than OLD_S ago, never one going on', () => {
  const now = 100_000
  const rows = pickRows([
    run('live', 'running', now - OLD_S * 3),
    run('fresh', 'done', now - OLD_S, { ended: now - OLD_S + 1 }),
    run('old', 'failed', now - OLD_S * 2, { ended: now - OLD_S - 1 }),
    run('oldstop', 'stopped', now - OLD_S * 2, { changed: now - OLD_S - 1 }),
  ], now)
  assert.deepEqual(Object.fromEntries(rows.map(r => [r.id, r.isOld])), { live: false, fresh: false, old: true, oldstop: true })
})

test('statusText counts running and starting runs, and failed or stopped ones, leaving out a zero part', () => {
  const now = 10_000
  const running = [run('r', 'running', 1), run('s', 'starting', 1)]
  const failed = [run('f', 'failed', 1, { ended: now - 60 })]
  assert.equal(statusText([], now), undefined)
  assert.equal(statusText(running, now), '● 2 running')
  assert.equal(statusText(failed, now), '✗ TT-01-list failed')
  assert.equal(statusText([...running, ...failed], now), '● 2 running · ✗ TT-01-list failed')
})

test('statusText names the run when it is the only one of its kind, else counts', () => {
  const now = 10_000
  const one = [run('r', 'running', 1, { name: 'build' })]
  const two = [run('f', 'failed', 1, { ended: now, name: null }), run('g', 'failed', 1, { ended: now })]
  assert.equal(statusText(one, now), '● build running')
  assert.equal(statusText([...one, two[0]], now), '● build running · ✗ f failed')
  assert.equal(statusText(two, now), '✗ 2 failed')
})

test('statusText counts a stopped run as failed, and ignores done and unknown runs', () => {
  const now = 10_000
  const rows = [run('st', 'stopped', 1, { changed: now - 5, name: 'job' }), run('d', 'done', 1, { ended: now }), run('u', 'unknown', 1)]
  assert.equal(statusText(rows, now), '✗ job failed')
})

test('statusText counts failed and stopped runs only for 60 minutes after they ended', () => {
  const now = 10_000
  const at = (ago: number) => [
    run('f', 'failed', 1, { ended: now - ago }),
    run('st', 'stopped', 1, { changed: now - ago }),
  ]
  assert.equal(statusText(at(3600), now), '✗ 2 failed')
  assert.equal(statusText(at(3601), now), undefined)
  assert.equal(statusText([run('f', 'failed', 1, { ended: null, changed: now - 10 })], now), '✗ TT-01-list failed')
})

test('statusText leaves out a failed or stopped run whose log was opened', () => {
  const now = 10_000
  const rows = [run('f', 'failed', 1, { ended: now }), run('st', 'stopped', 1, { changed: now, name: 'job' }), run('r', 'running', 1)]
  assert.equal(statusText(rows, now, new Set([rows[0].folder])), '● TT-01-list running · ✗ job failed')
  assert.equal(statusText(rows, now, new Set([rows[0].folder, rows[1].folder])), '● TT-01-list running')
  assert.equal(statusText(rows.slice(0, 2), now, new Set([rows[0].folder, rows[1].folder])), undefined)
})

test('endedRuns lists done, failed and stopped runs not yet notified', () => {
  const rows = [
    run('d', 'done', 1), run('f', 'failed', 1), run('st', 'stopped', 1),
    run('r', 'running', 1), run('s', 'starting', 1), run('u', 'unknown', 1), run('old', 'done', 1),
  ]
  const seen = new Set([rows[6].folder])
  assert.deepEqual(endedRuns(rows, seen).map(r => r.id), ['d', 'f', 'st'])
})

test('markNotified adds the ended folders newest last, and past the cap drops the oldest gone from disk only', () => {
  const ended = [run('new', 'done', 1)]
  assert.deepEqual(markNotified(['/a'], ended, new Set(), 3), ['/a', ended[0].folder])
  const kept = markNotified(['/on-disk', '/gone-1', '/gone-2'], ended, new Set(['/on-disk', ended[0].folder]), 3)
  assert.deepEqual(kept, ['/on-disk', '/gone-2', ended[0].folder])
  assert.deepEqual(markNotified(['/x', '/y'], ended, new Set(['/x', '/y', ended[0].folder]), 2), ['/x', '/y', ended[0].folder])
})

test('notifyText names the run, else its id, and how it ended', () => {
  assert.equal(notifyText(run('a', 'done', 1)), 'TT-01-list done')
  assert.equal(notifyText(run('a', 'failed', 1, { exit_code: 2 })), 'TT-01-list failed (exit 2)')
  assert.equal(notifyText(run('a', 'failed', 1, { exit_code: null })), 'TT-01-list failed')
  assert.equal(notifyText(run('agent-9', 'stopped', 1, { name: null })), 'agent-9 stopped')
})

test('newLiveRuns seeds on the first scan, then names only runs that start live after it', () => {
  const first = newLiveRuns([run('a', 'running', 1), run('b', 'done', 1)], null)
  assert.deepEqual(first.started, [])
  const second = newLiveRuns([run('a', 'running', 1), run('b', 'done', 1), run('c', 'starting', 2), run('d', 'done', 3)], first.known)
  assert.deepEqual(second.started.map(r => r.id), ['c'])
  const third = newLiveRuns([run('c', 'running', 2), run('e', 'stopped', 4)], second.known)
  assert.deepEqual(third.started, [])
  assert.deepEqual([...third.known].sort(), ['c', 'e'])
})

test('openPaneSetting reads settings get output, true unless it says false', () => {
  assert.equal(openPaneSetting(0, 'false\n'), false)
  assert.equal(openPaneSetting(0, 'true\n'), true)
  assert.equal(openPaneSetting(0, ''), true)
  assert.equal(openPaneSetting(1, 'false\n'), true)
  assert.equal(openPaneSetting(null, ''), true)
})

test('formatRow drops the remote-agent: prefix from the last step', () => {
  const [r] = pickRows([run('agent-1', 'done', 990, { step: 'remote-agent: no changes', ended: 995 })], 1000)
  assert.ok(formatRow(r).detail.startsWith('no changes · '), formatRow(r).detail)
})

test('metaLine shows the exit code once finished, the timings and the worktree folder', () => {
  const [done] = pickRows([run('a', 'done', 990, { ended: 995, exit_code: 0, worktree: '/tmp/wt-x/' })], 1000)
  assert.equal(metaLine(done), 'exit 0 · claim 3s · send 5s · first event 12s · wt-x')
  const [live] = pickRows([run('b', 'running', 990, { claim_s: null, send_s: null, first_event_s: null, worktree: null })], 1000)
  assert.equal(metaLine(live), 'no details yet')
  assert.equal(whenLine(done), '5s · <1m ago')
})
