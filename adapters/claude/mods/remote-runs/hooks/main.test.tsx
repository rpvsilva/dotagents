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
  /** What `ps` answers, or a function making the answer when it is called. */
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
  /** The `log` atom's writes. */
  logSets: number
  status: (string | undefined)[]
  notified: { title?: string; text: string }[]
  psCalls: string[][]
  /** Files too big to hold: a path's size, and what `tail` answers when asked from 1-based byte `+n` (null: it fails). */
  big: Record<string, { size: number; tail: (n: number) => string | null }>
  /** The argv of every `sh -c 'tail ... | head ...'` read. */
  tails: string[][]
  /** What `ui.copy` answers (an Error: it throws), what it was asked to copy where, and the toasts shown. */
  copy: unknown
  copies: { text: string; surface: string }[]
  toasts: string[]
  clock: ReturnType<typeof mock.clock>
  /** What `settings get remote open_pane` answers, the cwd of each such call, and every `ui.open`'s input. */
  setting: unknown
  settingDirs: string[]
  opens: any[]
}

function engine(on: any, over: Partial<World> = {}): World {
  const clock = mock.clock(on, { now: NOW })
  const w: World = {
    dirs: {}, files: {}, git: GIT_OK, ps: psAlive([]), placed: true, readFails: false, reads: [], opened: [], sets: 0, logSets: 0, psCalls: [], clock,
    big: {}, tails: [],
    sent: { isSent: true, channel: 'kitty' }, status: [], notified: [], copy: { isCopied: true }, copies: [], toasts: [],
    setting: { value: { exitCode: 0, stdout: 'true\n', stderr: '' } }, settingDirs: [], opens: [], ...over,
  }
  on('command.register', () => ({ value: { command: 'remote-runs' } }))
  on('session.start', (_: unknown, e: any) => ({ cwd: e.cwd }))
  on('session.cwd', () => ({ value: '/repo' }))
  on('ui.open', (_: unknown, e: any) => {
    w.opened.push(e.id)
    w.opens.push(e)
    return { value: { isPlaced: w.placed } }
  })
  on('process.run', (_: unknown, e: any) => {
    if (e.argv[0] === 'sh' && e.argv[2].includes('/.agents/bin/settings')) {
      w.settingDirs.push(e.argv[4])
      return w.setting
    }
    if (e.argv[0] === 'sh') {
      // sh -c 'tail -c "+$1" "$2" | head -c "$3"' sh <+n> <path> <max bytes>
      w.tails.push(e.argv)
      const out = w.big[e.argv[5]]?.tail(Number(e.argv[4]))
      if (out == null) return { value: { exitCode: 1, stdout: '', stderr: 'no such file' } }
      return { value: { exitCode: 0, stdout: out.slice(0, Number(e.argv[6])), stderr: '' } }
    }
    if (e.argv.includes('ps')) {
      w.psCalls.push(e.argv)
      return typeof w.ps === 'function' ? w.ps() : w.ps
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
  on('fs.stat', (_: unknown, e: any) => {
    const f = w.files[e.path]
    if (w.big[e.path]) return { value: { kind: 'file', size: w.big[e.path].size, mtimeMs: NOW, isLink: false } }
    if (!f) throw new Error(`ENOENT: ${e.path}`)
    return { value: { kind: 'file', size: new TextEncoder().encode(f.text).length, mtimeMs: f.mtimeMs, isLink: false } }
  })
  on('fs.read', (_: unknown, e: any) => {
    w.reads.push(e.path)
    const f = w.readFails ? undefined : w.files[e.path]
    if (!f) throw new Error(`ENOENT: ${e.path}`)
    return { value: f.text }
  })
  on('state.set', (_: unknown, e: any, next: any) => {
    if (e.key === 'runs') w.sets++
    if (e.key === 'log') w.logSets++
    return next(e)
  })
  on('ui.status', (_: unknown, e: any) => {
    w.status.push(e.text)
    return { value: undefined }
  })
  on('ui.copy', (_: unknown, e: any) => {
    w.copies.push({ text: e.text, surface: e.surface })
    if (w.copy instanceof Error) throw w.copy
    return { value: w.copy }
  })
  on('ui.toast', (_: unknown, e: any) => {
    w.toasts.push(e.text)
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
// Every Text the pane draws, but the follow bar's line of keys, which ends every log view.
const logTexts = (texts: any[]): string[] => texts.map((el: any) => el.text).filter((t: string) => !t.startsWith('b back · '))

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
    '● TT-01-list  running <1m', '●', 'running <1m', '     claim 3s · send 5s · first 12s · first agent event after 12s',
    '✓ main-run  done 1m23s · <1m ago', '✓', 'done 1m23s · <1m ago', '     claim 3s · send 5s · first 12s · done',
    '1-9 open a run · esc close',
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
    expect(await words($, surface)).toEqual(['? agent-20261102-100000-1  unknown', '?', 'unknown', '1-9 open a run · esc close'])
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
  expect(shown.filter(t => t.startsWith('■ '))).toEqual(['■ dead  stopped 0s · <1m ago', '■ reused  stopped 0s · <1m ago'])
  // In the C locale, as remote-agent writes pid_start: lstart follows the locale on macOS.
  expect(w.psCalls).toEqual([['env', 'LC_ALL=C', 'ps', '-o', 'pid=,lstart=', '-p', '7,8']])
})

test('a run left running after its remote-agent is gone stays stopped when ps cannot run', async ($, on) => {
  const w = engine(on, { ps: { deny: 'no processes' } })
  addRun(w, '/repo/.git', 'agent-1', runJson())
  await start($)
  await open($)
  expect((await words($))[0]).toBe('● TT-01-list  running <1m')
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
  expect((await words($))[0]).toBe('○ TT-01-list  starting <1m')

  write(w, '/repo/.git/remote-agent/agent-1/progress.log', 'claimed in 3s\n')
  await clock.advance(1500)
  expect((await words($))[3]).toBe('     claim 3s · send 5s · first 12s · claimed in 3s')

  write(w, '/repo/.git/remote-agent/agent-1/run.json', JSON.stringify(runJson({ state: 'done', exit_code: 0, ended: NOW / 1000 + 5 })))
  await clock.advance(1500)
  expect((await words($))[0]).toBe('✓ TT-01-list  done 35s · <1m ago')
})

test('a removed run folder leaves the pane, and a finished run is read once', async ($, on) => {
  const w = engine(on, { ps: psAlive([42]) })
  const { clock } = w
  addRun(w, '/repo/.git', 'agent-1', runJson())
  addRun(w, '/repo/.git', 'agent-2', runJson({ name: 'finished', state: 'done', exit_code: 0, ended: NOW / 1000 }), 'done\n')
  await start($)
  await clock.advance(1500)
  expect((await words($)).filter(t => /^. (TT-01-list|finished) /.test(t))).toEqual(['● TT-01-list  running <1m', '✓ finished  done 30s · <1m ago'])
  write(w, '/repo/.git/remote-agent/agent-2/progress.log', 'done\nlater line\n')
  w.dirs['/repo/.git/remote-agent'] = ['agent-2']
  const reads = w.reads.length
  await clock.advance(1500)
  expect(w.reads.length).toBe(reads)
  expect(await words($)).toEqual(['✓ finished  done 30s · <1m ago', '✓', 'done 30s · <1m ago', '     claim 3s · send 5s · first 12s · done', '1-9 open a run · esc close'])
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
  expect((await words($))[0]).toBe('? agent-1  unknown · 1m ago')
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
  expect(w.status).toEqual(['● TT-01-list running'])
  expect(w.notified).toEqual([])

  write(w, RUN_JSON, JSON.stringify(runJson({ state: 'done', exit_code: 0, ended: NOW / 1000 + 2 })))
  await w.clock.advance(1500)
  expect(w.notified).toEqual([{ title: 'remote run', text: 'TT-01-list done' }])
  expect(w.status).toEqual(['● TT-01-list running', undefined])

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
  expect(w.status).toEqual(['● TT-01-list running', '✗ TT-01-list failed'])

  await w.clock.advance(4500)
  expect(w.status.length).toBe(2)
  await w.clock.advance(7500)
  expect(w.status).toEqual(['● TT-01-list running', '✗ TT-01-list failed', undefined])
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
  expect(w.status).toEqual(['● TT-01-list running', '✗ TT-01-list failed'])
})

test('a run that ends between reading run.json and ps notifies how it ended, not stopped', async ($, on) => {
  const w = engine(on, { ps: psAlive([42]) })
  mock.store(on)
  addRun(w, '/repo/.git', 'agent-1', runJson())
  await start($)
  await w.clock.advance(1500)
  // The tick read run.json as running; remote-agent then writes done and exits before ps runs.
  w.ps = () => {
    write(w, RUN_JSON, JSON.stringify(runJson({ state: 'done', exit_code: 0, ended: NOW / 1000 + 2 })))
    return psAlive([])
  }
  await w.clock.advance(1500)
  expect(w.notified).toEqual([{ title: 'remote run', text: 'TT-01-list done' }])
  await w.clock.advance(3000)
  expect(w.notified.length).toBe(1)
  expect((await words($))[0]).toBe('✓ TT-01-list  done 32s · <1m ago')
})

test('more finished runs on disk than the store keeps are never notified again', async ($, on) => {
  const w = engine(on, { ps: psAlive([42]) })
  mock.store(on)
  for (let i = 0; i < 1005; i++) addRun(w, '/repo/.git', `agent-${String(i).padStart(4, '0')}`, runJson({ state: 'done', exit_code: 0, ended: NOW / 1000 - 100 }))
  await start($)
  await w.clock.advance(1500 * 4)
  expect(w.notified).toEqual([])
})

const NOTIFIED = 'notified:/repo/.git'

test('runs already finished at the first tick are not notified, nor are those an earlier process notified', async ($, on) => {
  const w = engine(on, { ps: psAlive([42]) })
  const earlier = '/repo/.git/remote-agent/agent-earlier'
  mock.store(on, { [NOTIFIED]: [earlier] })
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

test('without a store a run that ends is not notified, and the status line still follows it', async ($, on) => {
  const w = engine(on, { ps: psAlive([42]) })
  addRun(w, '/repo/.git', 'agent-1', runJson())
  await start($)
  await w.clock.advance(1500)
  write(w, RUN_JSON, JSON.stringify(runJson({ state: 'done', exit_code: 0, ended: NOW / 1000 + 2 })))
  await w.clock.advance(4500)
  expect(w.notified).toEqual([])
  expect(w.status).toEqual(['● TT-01-list running', undefined])
})

test('a notification that throws is marked all the same, and the store drops the oldest folders gone from disk past 1000', async ($, on) => {
  const w = engine(on, { ps: psAlive([42]) })
  // The oldest folder kept is still on disk: it stays, and a folder no longer there goes in its place.
  const present = '/repo/.git/remote-agent/agent-old'
  const kept: Record<string, unknown> = { [NOTIFIED]: [present, ...Array.from({ length: 999 }, (_, i) => `/repo/.git/remote-agent/gone-${i}`)] }
  const writes: unknown[][] = []
  on('store.get', (_: unknown, e: any) => ({ value: kept[e.key] }))
  on('store.set', (_: unknown, e: any) => {
    writes.push(e.value)
    kept[e.key] = e.value
    return { value: undefined }
  })
  Object.defineProperty(w, 'sent', { get: () => { throw new Error('refused') } })
  addRun(w, '/repo/.git', 'agent-old', runJson({ name: 'old', state: 'done', exit_code: 0, ended: NOW / 1000 - 100 }))
  addRun(w, '/repo/.git', 'agent-1', runJson())
  await start($)
  await w.clock.advance(1500)
  write(w, RUN_JSON, JSON.stringify(runJson({ state: 'done', exit_code: 0, ended: NOW / 1000 + 2 })))
  await w.clock.advance(4500)
  expect(w.notified).toEqual([{ title: 'remote run', text: 'TT-01-list done' }])
  expect(writes.length).toBe(1)
  expect(writes[0].length).toBe(1000)
  expect(writes[0][0]).toBe(present)
  expect(writes[0][1]).toBe('/repo/.git/remote-agent/gone-1')
  expect(writes[0][999]).toBe('/repo/.git/remote-agent/agent-1')
  await start($)
  await w.clock.advance(4500)
  expect(w.notified.length).toBe(1)
})

const TRANSCRIPT = '/repo/.git/remote-agent/agent-1/transcript.jsonl'
const jsonl = (...events: unknown[]) => events.map(e => `${JSON.stringify(e)}\n`).join('')
// Built from parts so no secret scanner flags this file.
const GHP = ['ghp', 'AbCdEfGhIjKlMnOpQrStUvWxYz0123456789'].join('_')

// A world with one running run, its rows drawn by a tick, and `transcript` as its transcript.jsonl (null: none).
async function withLog($: any, on: any, transcript: string | null) {
  const w = engine(on, { ps: psAlive([42]) })
  addRun(w, '/repo/.git', 'agent-1', runJson(), 'claimed in 3s\n')
  if (transcript === null) delete w.files[TRANSCRIPT]
  else w.files[TRANSCRIPT] = { text: transcript, mtimeMs: NOW }
  await start($)
  await w.clock.advance(1500)
  return w
}

test('pressing a run shows its log in order, and Back returns to the list with its rows', async ($, on) => {
  await withLog($, on, jsonl(
    { type: 'system', subtype: 'init', model: 'claude-opus' },
    { type: 'assistant', message: { content: [{ type: 'text', text: 'Reading the code.' }, { type: 'tool_use', id: 't1', name: 'Bash', input: { command: 'ls' } }] } },
    { type: 'user', message: { content: [{ type: 'tool_result', tool_use_id: 't1', content: 'a.txt\nb.txt' }] } },
    { type: 'result', subtype: 'success', is_error: false, duration_ms: 4000, total_cost_usd: 0.12 },
  ) + '{"type":"assistant","mess')
  const list = await words($)
  for (const surface of SURFACES) {
    const ui = await $.ui.mount({ ...PANE, surface })
    await ui.press({ key: 'run:agent-1' })
    expect((await ui.find({ key: 'back' }))?.props.hotkey).toBe('b')
    const shown = logTexts(await ui.findAll({ type: 'Text' }))
    expect(shown.slice(-5)).toEqual(['session started (claude-opus)', 'Reading the code.', '-> Bash ls', '<- Bash: a.txt', 'done in 4s, $0.12'])
    expect(shown).toContain(TRANSCRIPT)
    expect(shown.some((t: string) => t.includes('TT-01-list'))).toBe(true)
    await ui.press({ key: 'back' })
    await ui.unmount()
    expect(await words($, surface)).toEqual(list)
  }
})

test('a run with an empty, missing or unreadable transcript says it waits for the first event', async ($, on) => {
  const w = await withLog($, on, '')
  for (const transcript of ['', null, 'unreadable']) {
    if (transcript === null) delete w.files[TRANSCRIPT]
    w.readFails = transcript === 'unreadable'
    for (const surface of SURFACES) {
      const ui = await $.ui.mount({ ...PANE, surface })
      await ui.press({ key: 'run:agent-1' })
      const shown = logTexts(await ui.findAll({ type: 'Text' }))
      expect(shown[shown.length - 1]).toBe('waiting for the first event')
      await ui.press({ key: 'back' })
      await ui.unmount()
    }
  }
})

test('a token-shaped string in the transcript is masked in every line drawn', async ($, on) => {
  await withLog($, on, jsonl(
    { type: 'assistant', message: { content: [{ type: 'tool_use', id: 't1', name: 'Bash', input: { command: `echo ${GHP}` } }] } },
    { type: 'user', message: { content: [{ type: 'tool_result', tool_use_id: 't1', content: `\u001b[31m${GHP}\u001b[0m` }] } },
  ))
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await ui.press({ key: 'run:agent-1' })
  const shown = (await ui.findAll({})).map((el: any) => el.text ?? '')
  await ui.unmount()
  expect(shown.filter((t: string) => t.includes(GHP) || t.includes('\u001b'))).toEqual([])
  expect(shown).toContain('<- Bash: [masked]')
})

const say = (text: string) => jsonl({ type: 'assistant', message: { content: [{ type: 'text', text }] } })
const says = (count: number, from = 0) => Array.from({ length: count }, (_, i) => say(`line ${from + i}`)).join('')
const names = (count: number, from = 0) => Array.from({ length: count }, (_, i) => `line ${from + i}`)
// bodyRows 20, less the two header rows and a blank row, a blank row and the follow bar.
const ROWS = 15
const scroll = (by: number) => ({ component: 'Pane', requestId: 'remote-runs', offset: 0, by, bodyRows: 20, contentRows: 20, origin: { kind: 'person' } })

// The log lines the pane draws (each Text after the path, but the notes and the resume hint), the hint, and all its
// Text, as `surface` shows them.
async function logView($: any, surface: (typeof SURFACES)[number] = 'terminal') {
  const ui = await $.ui.mount({ ...PANE, surface })
  const texts = await ui.findAll({ type: 'Text' })
  await ui.unmount()
  const notes = ['(earlier events not shown: open the file)', 'waiting for the first event']
  const isHint = (t: any) => /^(to resume: |warning: |no session came back)/.test(t.text)
  // The log is what lies between the transcript path and the follow bar's key line.
  const keys = texts.findIndex((t: any) => t.text.startsWith('b back · '))
  const after = texts.slice(texts.findIndex((t: any) => t.text.endsWith('/transcript.jsonl')) + 1, keys < 0 ? undefined : keys)
  const lines = after.filter((t: any) => !notes.includes(t.text) && !isHint(t))
  for (const t of lines) expect(t.props.wrap).toBe('truncate-end')
  return { lines: lines.map((t: any) => t.text), hint: after.filter(isHint).map((t: any) => t.text), texts: texts.map((t: any) => t.text) }
}

async function press($: any, key: string) {
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await ui.press({ key })
  await ui.unmount()
}

function append(w: World, text: string) {
  write(w, TRANSCRIPT, w.files[TRANSCRIPT].text + text)
}

test('an open log draws exactly its rows and shows new events within one tick', async ($, on) => {
  const w = await withLog($, on, says(30))
  await press($, 'run:agent-1')
  expect((await logView($)).lines).toEqual(names(ROWS, 15))
  append(w, says(2, 30))
  await w.clock.advance(1500)
  for (const surface of SURFACES) expect((await logView($, surface)).lines).toEqual(names(ROWS, 17))
})

test('scrolled up, new events leave the window; End, or scrolling back to the end, follows again', async ($, on) => {
  const w = await withLog($, on, says(30))
  await press($, 'run:agent-1')
  await $.ui.scroll(scroll(-5))
  expect((await logView($)).lines).toEqual(names(ROWS, 10))
  append(w, says(3, 30))
  await w.clock.advance(1500)
  expect((await logView($)).lines).toEqual(names(ROWS, 10))

  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect((await ui.find({ key: 'end' }))?.props.hotkey).toBe('e')
  await ui.press({ key: 'end' })
  await ui.unmount()
  expect((await logView($)).lines).toEqual(names(ROWS, 18))
  append(w, says(1, 33))
  await w.clock.advance(1500)
  expect((await logView($)).lines).toEqual(names(ROWS, 19))

  await $.ui.scroll(scroll(-1))
  append(w, says(1, 34))
  await w.clock.advance(1500)
  expect((await logView($)).lines).toEqual(names(ROWS, 18))
  await $.ui.scroll(scroll(20))
  append(w, says(1, 35))
  await w.clock.advance(1500)
  expect((await logView($)).lines).toEqual(names(ROWS, 21))
})

test('a half-written last line is not drawn until whole, then drawn once', async ($, on) => {
  const w = await withLog($, on, says(2))
  const next = say('héllo → wörld')
  append(w, next.slice(0, 25))
  await press($, 'run:agent-1')
  expect((await logView($)).lines).toEqual(names(2))
  const sets = w.logSets
  const reads = w.reads.length
  await w.clock.advance(1500)
  await w.clock.advance(1500)
  expect((await logView($)).lines).toEqual(names(2))
  expect([w.logSets, w.reads.length]).toEqual([sets, reads])
  append(w, next.slice(25))
  await w.clock.advance(1500)
  await w.clock.advance(1500)
  expect((await logView($)).lines).toEqual([...names(2), 'héllo → wörld'])
})

test('a transcript over 4 MiB opens through tail, showing its last part under the earlier-events note', async ($, on) => {
  const w = await withLog($, on, null)
  const MiB = 1 << 20
  // The file: a 5 MiB head of one long line, then the events; tail answers from byte +n.
  let events = says(3)
  const head = 5 * MiB
  w.big[TRANSCRIPT] = {
    get size() { return head + events.length },
    tail: n => (n - 1 < head ? `${'x'.repeat(head - n)}\n` : '') + events.slice(Math.max(0, n - 1 - head)),
  }
  await press($, 'run:agent-1')
  const shown = await logView($)
  expect(shown.lines).toEqual(names(3))
  expect(shown.texts).toContain('(earlier events not shown: open the file)')
  expect(w.reads).not.toContain(TRANSCRIPT)
  expect(w.tails[0]).toEqual(['sh', '-c', 'tail -c "+$1" -- "$2" | head -c "$3"', 'sh', String(head + events.length - MiB), TRANSCRIPT, String(MiB + 1)])

  events += says(1, 3)
  await w.clock.advance(1500)
  expect((await logView($)).lines).toEqual(names(4))
  expect(w.tails[w.tails.length - 1][4]).toBe(String(head + says(3).length + 1))
})

test('switching runs or going Back starts the log again', async ($, on) => {
  const w = await withLog($, on, says(30))
  addRun(w, '/repo/.git', 'agent-2', runJson({ name: 'second' }))
  w.files['/repo/.git/remote-agent/agent-2/transcript.jsonl'] = { text: say('other run'), mtimeMs: NOW }
  await w.clock.advance(1500)
  await press($, 'run:agent-1')
  await $.ui.scroll(scroll(-5))
  await press($, 'back')
  append(w, says(1, 30))
  await press($, 'run:agent-1')
  expect((await logView($)).lines).toEqual(names(ROWS, 16))

  await $.ui.scroll(scroll(-5))
  await press($, 'back')
  await press($, 'run:agent-2')
  expect((await logView($)).lines).toEqual(['other run'])
})

const SESSION = '/repo/.git/remote-agent/agent-1/session.jsonl'
const RESUME = 'to resume: remote-agent resume agent-1 /repo'

// The Text lines the log of agent-1 shows on the terminal.
async function logLines($: any): Promise<string[]> {
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await ui.press({ key: 'run:agent-1' })
  const shown = logTexts(await ui.findAll({ type: 'Text' }))
  await ui.press({ key: 'back' })
  await ui.unmount()
  return shown
}

test('Copy path copies the exact transcript path on the pressing surface, and toasts the result', async ($, on) => {
  const w = await withLog($, on, '')
  for (const surface of ['terminal', 'desktop'] as const) {
    const ui = await $.ui.mount({ ...PANE, surface })
    await ui.press({ key: 'run:agent-1' })
    expect((await ui.find({ key: 'copy' }))?.props.hotkey).toBe('c')
    await ui.press({ key: 'copy' })
    w.copy = { isCopied: false, reason: 'no-clipboard' }
    await ui.press({ key: 'copy' })
    const shown = logTexts(await ui.findAll({ type: 'Text' }))
    expect(shown).toContain(TRANSCRIPT)
    await ui.press({ key: 'back' })
    await ui.unmount()
    w.copy = { isCopied: true }
  }
  expect(w.copies.map(c => [c.text, c.surface])).toEqual([
    [TRANSCRIPT, 'terminal'], [TRANSCRIPT, 'terminal'], [TRANSCRIPT, 'desktop'], [TRANSCRIPT, 'desktop'],
  ])
  expect(w.toasts.map(t => t.split(' (')[0])).toEqual(['copied', 'not copied', 'copied', 'not copied'])
})

test('a finished run with a session ends its log with how to resume it, and a running one does not', async ($, on) => {
  const w = await withLog($, on, jsonl({ type: 'result', subtype: 'success', is_error: false, duration_ms: 4000 }))
  w.files[SESSION] = { text: '{}\n', mtimeMs: NOW }
  let shown = await logLines($)
  expect(shown.filter((t: string) => t.startsWith('to resume') || t.startsWith('no session'))).toEqual([])

  write(w, RUN_JSON, JSON.stringify(runJson({ state: 'done', exit_code: 0, ended: NOW / 1000 + 2 })))
  await w.clock.advance(1500)
  shown = await logLines($)
  expect(shown.slice(-3, -1)).toEqual(['done in 4s', RESUME])
  expect(shown[shown.length - 1]).toMatch(/sandbox wrote.*forged turns.*session\.jsonl first$/)
})

test('a finished run with no session.jsonl says it cannot be resumed', async ($, on) => {
  const w = await withLog($, on, '')
  write(w, RUN_JSON, JSON.stringify(runJson({ state: 'failed', exit_code: 1, ended: NOW / 1000 + 2 })))
  await w.clock.advance(1500)
  const shown = await logLines($)
  expect(shown[shown.length - 1]).toBe('no session came back: this run cannot be resumed')
  expect(shown.filter((t: string) => t.startsWith('to resume'))).toEqual([])
})

test('a worktree path with spaces is quoted in the resume command', async ($, on) => {
  const w = await withLog($, on, '')
  w.files[SESSION] = { text: '{}\n', mtimeMs: NOW }
  write(w, RUN_JSON, JSON.stringify(runJson({ state: 'failed', exit_code: 1, ended: NOW / 1000 + 2, worktree: '/My Repo' })))
  await w.clock.advance(1500)
  expect(await logLines($)).toContain("to resume: remote-agent resume agent-1 '/My Repo'")
})

test('a copy that throws toasts not copied, and the path stays drawn', async ($, on) => {
  const w = await withLog($, on, '')
  w.copy = new Error('refused')
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await ui.press({ key: 'run:agent-1' })
  await ui.press({ key: 'copy' })
  expect(logTexts(await ui.findAll({ type: 'Text' }))).toContain(TRANSCRIPT)
  await ui.unmount()
  expect(w.toasts).toEqual(['not copied (refused): select the path shown instead'])
})

test('a stopped run shows how to resume it once its session.jsonl appears', async ($, on) => {
  const w = await withLog($, on, '')
  w.ps = psAlive([])
  await w.clock.advance(1500)
  expect((await logLines($)).slice(-1)).toEqual(['no session came back: this run cannot be resumed'])
  w.files[SESSION] = { text: '{}\n', mtimeMs: NOW }
  await w.clock.advance(1500)
  expect(await logLines($)).toContain(RESUME)
})

test('an open log whose run folder is removed shows the list again', async ($, on) => {
  const w = await withLog($, on, jsonl({ type: 'system', subtype: 'init', model: 'claude-opus' }))
  addRun(w, '/repo/.git', 'agent-2', runJson({ name: 'other', pid: 43 }))
  w.ps = psAlive([42, 43])
  await w.clock.advance(1500)
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await ui.press({ key: 'run:agent-1' })
  expect(await ui.find({ key: 'back' })).toBeTruthy()
  await ui.unmount()
  w.dirs['/repo/.git/remote-agent'] = ['agent-2']
  await w.clock.advance(1500)
  const shown = await words($)
  expect(shown).not.toContain(TRANSCRIPT)
  expect(shown.some((t: string) => t.startsWith('● other '))).toBe(true)
  expect(shown.some((t: string) => t.includes('TT-01-list'))).toBe(false)
})

test('a finished run draws fewer log lines, so its wrapped resume hint fits the body too', async ($, on) => {
  const w = await withLog($, on, says(30))
  w.files[SESSION] = { text: '{}\n', mtimeMs: NOW }
  write(w, RUN_JSON, JSON.stringify(runJson({ state: 'done', exit_code: 0, ended: NOW / 1000 + 2 })))
  await w.clock.advance(1500)
  await press($, 'run:agent-1')
  // 20 rows at 60 columns: two header rows and the follow bar with a blank row each, the command on one, the 152-character warning on three.
  const shown = await logView($)
  expect(shown.hint[0]).toBe(RESUME)
  expect(shown.lines).toEqual(names(11, 19))
  await $.ui.scroll(scroll(-1))
  expect((await logView($)).lines).toEqual(names(11, 18))
})

test('a result read on a later tick names the tool called before it, and a transcript written anew starts the log again', async ($, on) => {
  const call = jsonl({ type: 'assistant', message: { content: [{ type: 'tool_use', id: 't1', name: 'Bash', input: { command: 'ls' } }] } })
  const result = (text: string) => jsonl({ type: 'user', message: { content: [{ type: 'tool_result', tool_use_id: 't1', content: text }] } })
  const w = await withLog($, on, call)
  await press($, 'run:agent-1')
  expect((await logView($)).lines).toEqual(['-> Bash ls'])
  append(w, result('a.txt'))
  await w.clock.advance(1500)
  expect((await logView($)).lines).toEqual(['-> Bash ls', '<- Bash: a.txt'])
  // Shorter than what was read: a new file, whose result has no call before it.
  write(w, TRANSCRIPT, result('b.txt'))
  expect(result('b.txt').length).toBeLessThan((call + result('a.txt')).length)
  await w.clock.advance(1500)
  expect((await logView($)).lines).toEqual(['<- b.txt'])
})

test('a transcript between 1 and 3 MiB is read whole and opens at its last MiB under the earlier-events note', async ($, on) => {
  const MiB = 1 << 20
  const w = await withLog($, on, say('x'.repeat(2 * MiB)) + says(30))
  await press($, 'run:agent-1')
  const shown = await logView($)
  // 17 rows: the note takes one.
  expect(shown.lines).toEqual(names(ROWS - 1, 16))
  expect(shown.texts).toContain('(earlier events not shown: open the file)')
  expect(w.tails).toEqual([])
  // Read on from where it stopped: a window scrolled up stays put.
  await $.ui.scroll(scroll(-5))
  append(w, says(1, 30))
  await w.clock.advance(1500)
  expect((await logView($)).lines).toEqual(names(ROWS - 1, 11))
  await press($, 'end')
  expect((await logView($)).lines).toEqual(names(ROWS - 1, 17))
})

test('a tail read that fails leaves the log waiting, and the next tick reads it', async ($, on) => {
  const w = await withLog($, on, null)
  const events = says(3)
  const head = 5 * (1 << 20)
  let isUp = false
  w.big[TRANSCRIPT] = {
    size: head + events.length,
    tail: n => (isUp ? `${'x'.repeat(head - n)}\n${events}` : null),
  }
  await press($, 'run:agent-1')
  let shown = await logView($)
  expect(shown.lines).toEqual([])
  expect(shown.texts).toContain('waiting for the first event')
  expect(shown.texts).not.toContain('(earlier events not shown: open the file)')
  isUp = true
  await w.clock.advance(1500)
  shown = await logView($)
  expect(shown.lines).toEqual(names(3))
  expect(shown.texts).toContain('(earlier events not shown: open the file)')
})

const DONE = jsonl({ type: 'result', subtype: 'success', is_error: false, duration_ms: 4000 })
const GAPS = {
  'events over a chunk': says(1300, 2).replace(/line /g, `${'y'.repeat(900)} `),
  'one line over a chunk': say('x'.repeat((1 << 20) + 1000)),
}
for (const [gap, text] of Object.entries(GAPS)) {
  test(`a finished run that wrote ${gap} between two ticks shows its log to the end`, async ($, on) => {
    const w = await withLog($, on, says(2))
    await press($, 'run:agent-1')
    expect((await logView($)).lines).toEqual(names(2))
    append(w, text + DONE)
    write(w, RUN_JSON, JSON.stringify(runJson({ state: 'done', exit_code: 0, ended: NOW / 1000 + 2 })))
    for (let i = 0; i < 4; i++) await w.clock.advance(1500)
    expect((await logView($)).lines.slice(-1)).toEqual(['done in 4s'])
  })
}

for (const [session, isResumable] of [['', false], ['{}\n', true]] as const) {
  test(`a finished run with ${isResumable ? 'a non-empty' : 'an empty'} session.jsonl ${isResumable ? 'shows the' : 'shows no'} resume command`, async ($, on) => {
    const w = await withLog($, on, '')
    w.files[SESSION] = { text: session, mtimeMs: NOW }
    write(w, RUN_JSON, JSON.stringify(runJson({ state: 'done', exit_code: 0, ended: NOW / 1000 + 2 })))
    await w.clock.advance(1500)
    const shown = await logLines($)
    expect(shown.includes(RESUME)).toBe(isResumable)
    if (!isResumable) expect(shown[shown.length - 1]).toBe('no session came back: this run cannot be resumed')
  })
}

test('scrolling a finished run before its log is drawn moves by the rows the draw shows', async ($, on) => {
  // Once `opened` is set, the view the mod reads says that run is open with no press, so nothing drew its log.
  let opened: string | null = null
  on('state.get', (_: unknown, e: any, next: any) =>
    e.key === 'view' && opened !== null ? { value: { value: { shape: 'view-1', value: { runId: opened } }, version: 1 } } : next(e))
  const w = await withLog($, on, '')
  // A run no other test draws, opened with no draw, as a reload finds it.
  addRun(w, '/repo/.git', 'undrawn', runJson({ name: 'undrawn', state: 'done', exit_code: 0, ended: NOW / 1000 + 2 }))
  w.files['/repo/.git/remote-agent/undrawn/transcript.jsonl'] = { text: says(30), mtimeMs: NOW }
  w.files['/repo/.git/remote-agent/undrawn/session.jsonl'] = { text: '{}\n', mtimeMs: NOW }
  await w.clock.advance(1500)
  await words($)
  opened = 'undrawn'
  await w.clock.advance(1500)
  await $.ui.scroll(scroll(-1))
  // 20 rows at 60 columns: the header, the follow bar, their blank rows and the hint's four leave 11, one up from the end.
  expect((await logView($)).lines).toEqual(names(11, 18))
  await $.ui.scroll(scroll(-1))
  expect((await logView($)).lines).toEqual(names(11, 17))
})

test('runs that ended over 30 minutes ago are drawn dim, on one line, under an older rule', async ($, on) => {
  const w = engine(on, { ps: psAlive([42]) })
  const now = NOW / 1000
  addRun(w, '/repo/.git', 'agent-live', runJson({ name: 'live', started: now - 5 }), 'claimed in 3s\n')
  addRun(w, '/repo/.git', 'agent-fresh', runJson({ name: 'fresh', state: 'done', exit_code: 0, started: now - 600, ended: now - 300 }), 'done\n')
  addRun(w, '/repo/.git', 'agent-old', runJson({ name: 'old', state: 'failed', exit_code: 1, started: now - 7300, ended: now - 7200 }), 'failed\n')
  await start($)
  await open($)
  expect(await words($)).toEqual([
    '● live  running <1m', '●', 'running <1m', '     claim 3s · send 5s · first 12s · claimed in 3s',
    '✓ fresh  done 5m00s · 5m ago', '✓', 'done 5m00s · 5m ago', '     claim 3s · send 5s · first 12s · done',
    '── older ──', '✗ old  failed 1m40s · 2h ago', '✗', 'failed 1m40s · 2h ago',
    '1-9 open a run · esc close',
  ])
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  const dim = async (key: string) => (await ui.find({ key }))?.props.dimColor ?? false
  expect([await dim('run:agent-live'), await dim('run:agent-fresh'), await dim('run:agent-old')]).toEqual([false, false, true])
  const glyphs = (await ui.findAll({ type: 'Text' })).filter((t: any) => ['●', '✓', '✗'].includes(t.text))
  expect(glyphs.map((t: any) => [t.text, t.props.color ?? null, t.props.dimColor ?? false])).toEqual([
    ['●', 'warning', false], ['✓', 'success', false], ['✗', null, true],
  ])
  await ui.unmount()
})

test('the follow bar says it follows, offers Jump to end as the main action once scrolled up, and colors lines', async ($, on) => {
  await withLog($, on, jsonl(
    { type: 'assistant', message: { content: [{ type: 'tool_use', id: 't1', name: 'Bash', input: { command: 'ls' } }] } },
    { type: 'user', message: { content: [{ type: 'tool_result', tool_use_id: 't1', is_error: true, content: 'boom' }] } },
  ) + says(30))
  await press($, 'run:agent-1')
  const bar = async () => {
    const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
    const end = await ui.find({ key: 'end' })
    const texts = await ui.findAll({ type: 'Text' })
    await ui.unmount()
    return { label: end?.text, variant: end?.props.variant ?? null, hotkey: end?.props.hotkey, texts }
  }
  expect(await bar()).toMatchObject({ label: '● Following live', variant: null, hotkey: 'e' })
  await $.ui.scroll(scroll(-40))
  const up = await bar()
  expect(up).toMatchObject({ label: '↓ Jump to end', variant: 'primary', hotkey: 'e' })
  const style = (text: string) => up.texts.find((t: any) => t.text === text)?.props.color ?? null
  expect([style('-> Bash ls'), style('<- ERROR Bash: boom'), style('line 0')]).toEqual(['suggestion', 'error', null])
  await press($, 'end')
  expect((await bar()).label).toBe('● Following live')
})

test('a run that starts while the session is open opens the pane, without the keyboard, once', async ($, on) => {
  const w = engine(on, { ps: psAlive([42, 43]) })
  addRun(w, '/repo/.git', 'agent-before', runJson({ name: 'before', pid: 43 }))
  await start($)
  await w.clock.advance(1500)
  // Already going at the first tick: no open.
  expect(w.opened).toEqual([])
  addRun(w, '/repo/.git', 'agent-new', runJson({ state: 'starting' }))
  await w.clock.advance(1500)
  expect(w.opens.map(o => [o.id, o.focus ?? null, o.closeOnEscape ?? null])).toEqual([['remote-runs', null, true]])
  expect(w.settingDirs).toEqual(['/repo'])
  write(w, '/repo/.git/remote-agent/agent-new/run.json', JSON.stringify(runJson({ state: 'running' })))
  await w.clock.advance(1500)
  await w.clock.advance(1500)
  expect(w.opened).toEqual(['remote-runs'])
})

test('remote.open_pane false keeps the pane closed when a run starts; a failing settings opens it', async ($, on) => {
  const w = engine(on, { ps: psAlive([42]), setting: { value: { exitCode: 0, stdout: 'false\n', stderr: '' } } })
  await start($)
  await w.clock.advance(1500)
  addRun(w, '/repo/.git', 'agent-1', runJson())
  await w.clock.advance(1500)
  expect(w.settingDirs).toEqual(['/repo'])
  expect(w.opened).toEqual([])
  w.setting = { value: { exitCode: 127, stdout: '', stderr: 'not found' } }
  addRun(w, '/repo/.git', 'agent-2', runJson({ pid: 42, name: 'second' }))
  await w.clock.advance(1500)
  expect(w.opened).toEqual(['remote-runs'])
})

test('opening a failed run\'s log drops it from the status line; a done run\'s changes nothing', async ($, on) => {
  const w = engine(on, { ps: psAlive([42]) })
  const now = NOW / 1000
  addRun(w, '/repo/.git', 'agent-live', runJson({ name: 'live' }))
  addRun(w, '/repo/.git', 'agent-bad', runJson({ name: 'bad', state: 'failed', exit_code: 1, ended: now - 60, pid: 7 }))
  addRun(w, '/repo/.git', 'agent-ok', runJson({ name: 'ok', state: 'done', exit_code: 0, ended: now - 60, pid: 8 }))
  await start($)
  await w.clock.advance(1500)
  expect(w.status.at(-1)).toBe('● live running · ✗ bad failed')
  await press($, 'run:agent-ok')
  await press($, 'back')
  await w.clock.advance(1500)
  expect(w.status.at(-1)).toBe('● live running · ✗ bad failed')
  await press($, 'run:agent-bad')
  await w.clock.advance(1500)
  expect(w.status.at(-1)).toBe('● live running')
})
