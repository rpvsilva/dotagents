import { expect, mock, test } from 'claude-code/testing'

const PANE = {
  plugin: 'remote-runs',
  component: 'Pane',
  requestId: 'remote-runs',
  props: {
    title: 'Remote runs',
    isFocused: true,
    bodyColumns: 60,
    placement: 'dock',
    scroll: { offset: 0, bodyRows: 20 },
    view: {},
  },
} as const
const SURFACES = ['terminal', 'desktop', 'vscode', 'mobile'] as const
const GIT_OK = { value: { exitCode: 0, stdout: '/repo/.git\n', stderr: '' } }
const LSTART = 'Mon Nov  2 10:00:00 2026'
const NOW = 2_000_000_000_000 // ms

// The world beneath the mod, which a test changes between ticks: `dirs` maps a path to its folders, `files` a
// file's path to its text and mtime (a path neither holds is missing), `git` and `ps` answer `process.run`.
type World = {
  dirs: Record<string, string[]>
  files: Record<string, { text: string; mtimeMs: number }>
  git: unknown
  ps: unknown
  placed: boolean
  opened: string[]
  readFails: boolean
  /** The paths `fs.read` was asked for. */
  reads: string[]
  /** What `ui.notify` answers. */
  sent: unknown
  /** The `runs` atom's writes, the status lines set and the notifications raised. */
  sets: number
  status: (string | undefined)[]
  notified: { title?: string; text: string }[]
  psCalls: string[][]
  clock: ReturnType<typeof mock.clock>
}

function engine(on: any, over: Partial<World> = {}): World {
  const clock = mock.clock(on, { now: NOW })
  const w: World = {
    dirs: {}, files: {}, git: GIT_OK, ps: psAlive([]), placed: true, readFails: false, reads: [], opened: [], sets: 0, psCalls: [], clock,
    sent: { isSent: true, channel: 'kitty' }, status: [], notified: [], ...over,
  }
  on('command.register', () => ({ value: { command: 'remote-runs' } }))
  on('session.start', (_: unknown, e: any) => ({ cwd: e.cwd }))
  on('session.cwd', () => ({ value: '/repo' }))
  on('ui.open', (_: unknown, e: any) => {
    w.opened.push(e.id)
    return { value: { isPlaced: w.placed } }
  })
  on('process.run', (_: unknown, e: any) => {
    if (e.argv[0] === 'ps') {
      w.psCalls.push(e.argv)
      return w.ps
    }
    return w.git
  })
  on('fs.list', (_: unknown, e: any) => {
    const files = Object.keys(w.files).filter(p => p.slice(0, p.lastIndexOf('/')) === e.path)
    const names = w.dirs[e.path]
    if (!names && files.length === 0) throw new Error(`ENOENT: ${e.path}`)
    const dir = (name: string) => ({ name, kind: 'dir', size: 0, mtimeMs: 0, isLink: false })
    const file = (p: string) => ({
      name: p.slice(p.lastIndexOf('/') + 1), kind: 'file', size: w.files[p].text.length, mtimeMs: w.files[p].mtimeMs, isLink: false,
    })
    return { value: [...files.map(file), ...(names ?? []).map(dir)] }
  })
  on('fs.read', (_: unknown, e: any) => {
    w.reads.push(e.path)
    const f = w.readFails ? undefined : w.files[e.path]
    if (!f) throw new Error(`ENOENT: ${e.path}`)
    return { value: f.text }
  })
  on('state.set', (_: unknown, e: any, next: any) => {
    if (e.key === 'runs') w.sets++
    return next(e)
  })
  on('ui.status', (_: unknown, e: any) => {
    w.status.push(e.text)
    return { value: undefined }
  })
  on('ui.notify', (_: unknown, e: any) => {
    w.notified.push({ title: e.title, text: e.text })
    return { value: w.sent }
  })
  return w
}

function psAlive(pids: number[], start = LSTART) {
  return { value: { exitCode: pids.length ? 0 : 1, stdout: pids.map(p => `  ${p} ${start}\n`).join(''), stderr: '' } }
}

// Puts a run folder `<git dir>/remote-agent/<id>/` in the world, with run.json (unless `json` is null) and
// progress.log (unless `progress` is null).
function addRun(w: World, gitDir: string, id: string, json: Record<string, unknown> | null, progress: string | null = null) {
  const parent = `${gitDir}/remote-agent`
  w.dirs[parent] = [...(w.dirs[parent] ?? []), id]
  w.dirs[`${parent}/${id}`] = []
  if (json) write(w, `${parent}/${id}/run.json`, JSON.stringify(json))
  if (progress !== null) write(w, `${parent}/${id}/progress.log`, progress)
  w.files[`${parent}/${id}/transcript.jsonl`] = { text: '', mtimeMs: NOW }
}

function write(w: World, path: string, text: string) {
  w.files[path] = { text, mtimeMs: (w.files[path]?.mtimeMs ?? NOW - 60_000) + 1000 }
}

function runJson(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    run: 'agent-1', name: 'TT-01-list', worktree: '/repo', started: NOW / 1000 - 30, pid: 42, pid_start: LSTART,
    state: 'running', exit_code: null, ended: null, claim_s: 3, send_s: 5, first_event_s: 12, ...over,
  }
}

async function start($: any) {
  await $.session.start({ cwd: '/repo', surface: 'terminal', isInteractive: true })
}

// Runs /remote-runs and checks its answer: a hook that threw would be skipped, with a failed-hook line.
async function open($: any) {
  const { text } = await $.command.run({ command: 'remote-runs' })
  expect(text).toBe('Remote runs pane opened.')
}

// Every word the pane shows on `surface`, in order: Button labels and Text.
async function words($: any, surface: (typeof SURFACES)[number] = 'terminal'): Promise<string[]> {
  const ui = await $.ui.mount({ ...PANE, surface })
  const found = (await ui.findAll({})).filter((el: any) => el.type === 'Button' || el.type === 'Text')
  const seen = found.map((el: any) => el.text)
  await ui.unmount()
  return seen
}

test('/remote-runs opens the remote-runs pane', async ($, on) => {
  const w = engine(on)
  await start($)
  await open($)
  expect(w.opened).toEqual(['remote-runs'])
})

test('the pane says no remote runs when the run folders are missing, outside git, or without processes', async ($, on) => {
  const w = engine(on)
  for (const git of [GIT_OK, { value: { exitCode: 128, stdout: '', stderr: 'fatal' } }, { deny: 'no processes' }]) {
    w.git = git
    await start($)
    await open($)
    for (const surface of SURFACES) expect(await words($, surface)).toEqual(['no remote runs in this repo'])
  }
})

test('the pane lists runs of the main git dir and of each linked worktree, with every column', async ($, on) => {
  const w = engine(on, { ps: psAlive([42]) })
  w.dirs['/repo/.git/worktrees'] = ['w1']
  addRun(w, '/repo/.git', 'agent-main', runJson({ name: 'main-run', state: 'done', exit_code: 0, ended: NOW / 1000 - 10, started: NOW / 1000 - 93 }), 'done\n')
  addRun(w, '/repo/.git/worktrees/w1', 'agent-wt', runJson(), 'claimed in 3s\nfirst agent event after 12s\n')
  w.files['/repo/.git/remote-agent/notes.txt'] = { text: 'x', mtimeMs: 0 }
  await start($)
  await open($)
  expect(await words($)).toEqual([
    'TT-01-list running <1m', 'running <1m', '   claim 3s · send 5s · first 12s · first agent event after 12s',
    'main-run done 1m23s', 'done 1m23s', '   claim 3s · send 5s · first 12s · done',
  ])
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect((await ui.find({ key: 'run:agent-wt' }))?.props.hotkey).toBe('1')
  expect((await ui.find({ key: 'run:agent-main' }))?.props.hotkey).toBe('2')
  await ui.unmount()
})

test('a folder with no run.json, or an empty transcript, shows a row and nothing fails', async ($, on) => {
  const w = engine(on)
  addRun(w, '/repo/.git', 'agent-20261102-100000-1', null)
  for (const surface of SURFACES) {
    await start($)
    await open($)
    expect(await words($, surface)).toEqual(['agent-20261102-100000-1 unknown', 'unknown'])
  }
  expect(w.psCalls).toEqual([])
})

test('a running run whose pid is dead or started at another time shows stopped', async ($, on) => {
  const w = engine(on, { ps: psAlive([]) })
  addRun(w, '/repo/.git', 'agent-dead', runJson({ name: 'dead', pid: 7 }))
  addRun(w, '/repo/.git', 'agent-reused', runJson({ name: 'reused', pid: 8, started: NOW / 1000 - 40 }))
  w.ps = psAlive([8], 'Tue Nov  3 09:00:00 2026')
  await start($)
  await open($)
  const shown = await words($)
  expect(shown.filter(t => / stopped/.test(t) && !t.startsWith(' '))).toEqual(['dead stopped 0s', 'reused stopped 0s'])
  expect(w.psCalls).toEqual([['ps', '-o', 'pid=,lstart=', '-p', '7,8']])
})

test('a run left running after its remote-agent is gone stays stopped when ps cannot run', async ($, on) => {
  const w = engine(on, { ps: { deny: 'no processes' } })
  addRun(w, '/repo/.git', 'agent-1', runJson())
  await start($)
  await open($)
  expect((await words($))[0]).toBe('TT-01-list running <1m')
})

test('more than 20 runs keep every unfinished one and drop the oldest finished', async ($, on) => {
  const w = engine(on, { ps: psAlive([42]) })
  for (let i = 0; i < 25; i++) {
    addRun(w, '/repo/.git', `agent-f${String(i).padStart(2, '0')}`, runJson({ name: `f${i}`, state: 'done', exit_code: 0, started: 1000 + i, ended: 1001 + i }))
  }
  addRun(w, '/repo/.git', 'agent-old', runJson({ name: 'old-running', started: 1 }))
  await start($)
  await open($)
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  const buttons = await ui.findAll({ type: 'Button' })
  const keys = buttons.map((b: any) => b.key)
  const hotkeys = buttons.map((b: any) => b.props.hotkey)
  await ui.unmount()
  expect(keys.length).toBe(20)
  expect(keys[0]).toBe('run:agent-old')
  expect(keys[1]).toBe('run:agent-f24')
  expect(keys[19]).toBe('run:agent-f06')
  expect(hotkeys).toEqual(['1', '2', '3', '4', '5', '6', '7', '8', '9', ...Array(11).fill(undefined)])
})

test('a changed run.json or a new progress line shows after one tick, with no command typed', async ($, on) => {
  const w = engine(on, { ps: psAlive([42]) })
  const { clock } = w
  await start($)
  await clock.advance(1500)
  expect(await words($)).toEqual(['no remote runs in this repo'])

  addRun(w, '/repo/.git', 'agent-1', runJson({ state: 'starting' }))
  await clock.advance(1500)
  expect((await words($))[0]).toBe('TT-01-list starting <1m')

  write(w, '/repo/.git/remote-agent/agent-1/progress.log', 'claimed in 3s\n')
  await clock.advance(1500)
  expect((await words($))[2]).toBe('   claim 3s · send 5s · first 12s · claimed in 3s')

  write(w, '/repo/.git/remote-agent/agent-1/run.json', JSON.stringify(runJson({ state: 'done', exit_code: 0, ended: NOW / 1000 + 5 })))
  await clock.advance(1500)
  expect((await words($))[0]).toBe('TT-01-list done 35s')
})

test('a removed run folder leaves the pane, and a finished run is read once', async ($, on) => {
  const w = engine(on, { ps: psAlive([42]) })
  const { clock } = w
  addRun(w, '/repo/.git', 'agent-1', runJson())
  addRun(w, '/repo/.git', 'agent-2', runJson({ name: 'finished', state: 'done', exit_code: 0, ended: NOW / 1000 }), 'done\n')
  await start($)
  await clock.advance(1500)
  expect((await words($)).filter(t => /^(TT-01-list|finished) /.test(t))).toEqual(['TT-01-list running <1m', 'finished done 30s'])
  write(w, '/repo/.git/remote-agent/agent-2/progress.log', 'done\nlater line\n')
  w.dirs['/repo/.git/remote-agent'] = ['agent-2']
  const reads = w.reads.length
  await clock.advance(1500)
  expect(w.reads.length).toBe(reads)
  expect(await words($)).toEqual(['finished done 30s', 'done 30s', '   claim 3s · send 5s · first 12s · done'])
})

test('an unchanged tick does not set state', async ($, on) => {
  const w = engine(on, { ps: psAlive([42]) })
  const { clock } = w
  addRun(w, '/repo/.git', 'agent-1', runJson(), 'claimed in 3s\n')
  await start($)
  await clock.advance(1500)
  const after = w.sets
  expect(after).toBe(1)
  await clock.advance(1500)
  await clock.advance(1500)
  expect(w.sets).toBe(after)
})

test('failing file reads and processes on every tick leave no failed hook', async ($, on) => {
  const w = engine(on, { ps: { deny: 'no processes' }, readFails: true })
  addRun(w, '/repo/.git', 'agent-1', runJson())
  await start($)
  await w.clock.advance(1500)
  await open($)
  expect((await words($))[0]).toBe('agent-1 unknown')
})

test('/remote-runs says the pane waits for a wider terminal when it is not placed', async ($, on) => {
  engine(on, { placed: false })
  await start($)
  const { text } = await $.command.run({ command: 'remote-runs' })
  expect(text).toBe('Remote runs pane opens once the terminal is wider.')
})

const RUN_JSON = '/repo/.git/remote-agent/agent-1/run.json'

test('a run that ends notifies once and clears the status line, with the pane closed', async ($, on) => {
  const w = engine(on, { ps: psAlive([42]) })
  mock.store(on)
  addRun(w, '/repo/.git', 'agent-1', runJson())
  await start($)
  await w.clock.advance(1500)
  expect(w.status).toEqual(['remote: 1 running'])
  expect(w.notified).toEqual([])

  write(w, RUN_JSON, JSON.stringify(runJson({ state: 'done', exit_code: 0, ended: NOW / 1000 + 2 })))
  await w.clock.advance(1500)
  expect(w.notified).toEqual([{ title: 'remote run', text: 'TT-01-list done' }])
  expect(w.status).toEqual(['remote: 1 running', undefined])

  await w.clock.advance(1500)
  await start($)
  await w.clock.advance(3000)
  expect(w.notified.length).toBe(1)
  expect(w.status.length).toBe(2)
  expect(w.opened).toEqual([])
})

test('a run that fails notifies with its exit code and counts as failed in the status line', async ($, on) => {
  const w = engine(on, { ps: psAlive([42]) })
  mock.store(on)
  addRun(w, '/repo/.git', 'agent-1', runJson())
  await start($)
  await w.clock.advance(1500)
  // It ended 59m50s ago as this tick sees it: the status line drops it ten seconds on.
  write(w, RUN_JSON, JSON.stringify(runJson({ state: 'failed', exit_code: 2, ended: NOW / 1000 + 3 - 3590 })))
  await w.clock.advance(1500)
  expect(w.notified).toEqual([{ title: 'remote run', text: 'TT-01-list failed (exit 2)' }])
  expect(w.status).toEqual(['remote: 1 running', 'remote: 1 failed'])

  await w.clock.advance(4500)
  expect(w.status.length).toBe(2)
  await w.clock.advance(7500)
  expect(w.status).toEqual(['remote: 1 running', 'remote: 1 failed', undefined])
  expect(w.notified.length).toBe(1)
})

test('a run whose remote-agent died notifies stopped', async ($, on) => {
  const w = engine(on, { ps: psAlive([42]) })
  mock.store(on)
  addRun(w, '/repo/.git', 'agent-1', runJson())
  await start($)
  await w.clock.advance(1500)
  w.ps = psAlive([])
  await w.clock.advance(1500)
  expect(w.notified).toEqual([{ title: 'remote run', text: 'TT-01-list stopped' }])
  expect(w.status).toEqual(['remote: 1 running', 'remote: 1 failed'])
})

test('runs already finished at the first tick are not notified, nor are those an earlier process notified', async ($, on) => {
  const w = engine(on, { ps: psAlive([42]) })
  const earlier = '/repo/.git/remote-agent/agent-earlier'
  mock.store(on, { notified: [earlier] })
  addRun(w, '/repo/.git', 'agent-earlier', runJson({ name: 'earlier', state: 'done', exit_code: 0, ended: NOW / 1000 - 100 }))
  addRun(w, '/repo/.git', 'agent-old', runJson({ name: 'old', state: 'failed', exit_code: 1, ended: NOW / 1000 - 5000 }))
  addRun(w, '/repo/.git', 'agent-1', runJson())
  await start($)
  await w.clock.advance(1500)
  await w.clock.advance(1500)
  expect(w.notified).toEqual([])

  write(w, RUN_JSON, JSON.stringify(runJson({ state: 'done', exit_code: 0, ended: NOW / 1000 + 2 })))
  await w.clock.advance(1500)
  expect(w.notified).toEqual([{ title: 'remote run', text: 'TT-01-list done' }])
})

test('a notification no channel sent is not tried again', async ($, on) => {
  const w = engine(on, { ps: psAlive([42]), sent: { isSent: false, reason: 'disabled' } })
  mock.store(on)
  addRun(w, '/repo/.git', 'agent-1', runJson())
  await start($)
  await w.clock.advance(1500)
  write(w, RUN_JSON, JSON.stringify(runJson({ state: 'done', exit_code: 0, ended: NOW / 1000 + 2 })))
  await w.clock.advance(1500)
  await w.clock.advance(1500)
  await w.clock.advance(1500)
  expect(w.notified.length).toBe(1)
})

test('with no run the status line is never set', async ($, on) => {
  const w = engine(on)
  mock.store(on)
  await start($)
  await w.clock.advance(4500)
  expect(w.status).toEqual([])
})
