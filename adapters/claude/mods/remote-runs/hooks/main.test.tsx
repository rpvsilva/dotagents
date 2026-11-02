import { expect, test } from 'claude-code/testing'

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

// Stands for the engine beneath the mod: `git` answers the `process.run` call, `dirs` maps a listed path to its
// folders (a path it lacks is missing). Returns the pane ids `ui.open` was asked for.
function engine(on: any, git: (e: any) => unknown, dirs: Record<string, string[]>): string[] {
  const opened: string[] = []
  on('command.register', () => ({ value: { command: 'remote-runs' } }))
  on('session.start', (_: unknown, e: any) => ({ cwd: e.cwd }))
  on('session.cwd', () => ({ value: '/repo' }))
  on('ui.open', (_: unknown, e: any) => {
    opened.push(e.id)
    return { value: { isPlaced: true } }
  })
  on('process.run', (_: unknown, e: any) => git(e))
  on('fs.list', (_: unknown, e: any) => {
    const names = dirs[e.path]
    if (!names) throw new Error(`ENOENT: ${e.path}`)
    return { value: names.map(name => ({ name, kind: 'dir', size: 0, mtimeMs: 0, isLink: false })) }
  })
  return opened
}

// Runs /remote-runs, then reads the pane's words on every surface. The command's own answer proves its hook ran
// to the end: a hook that threw would be skipped, with a failed-hook line.
async function paneTexts($: any): Promise<string[]> {
  await $.session.start({ cwd: '/repo', surface: 'terminal', isInteractive: true })
  const { text } = await $.command.run({ command: 'remote-runs' })
  expect(text).toBe('Remote runs pane opened.')
  const seen: string[] = []
  for (const surface of SURFACES) {
    const ui = await $.ui.mount({ ...PANE, surface })
    seen.push((await ui.find({ type: 'Text' }))?.text ?? '')
    await ui.unmount()
  }
  return seen
}

test('/remote-runs opens the remote-runs pane', async ($, on) => {
  const opened = engine(on, () => GIT_OK, {})
  await $.session.start({ cwd: '/repo', surface: 'terminal', isInteractive: true })
  await $.command.run({ command: 'remote-runs' })
  expect(opened).toEqual(['remote-runs'])
})

test('the pane says no remote runs when the run folders are missing', async ($, on) => {
  engine(on, () => GIT_OK, {})
  expect(await paneTexts($)).toEqual(SURFACES.map(() => 'no remote runs in this repo'))
})

test('the pane says no remote runs outside a git repo', async ($, on) => {
  engine(on, () => ({ value: { exitCode: 128, stdout: '', stderr: 'fatal: not a git repository' } }), {})
  expect(await paneTexts($)).toEqual(SURFACES.map(() => 'no remote runs in this repo'))
})

test('the pane says no remote runs when git cannot run', async ($, on) => {
  engine(on, () => ({ deny: 'no processes on this surface' }), {})
  expect(await paneTexts($)).toEqual(SURFACES.map(() => 'no remote runs in this repo'))
})

test('the pane lists a run folder from a linked worktree', async ($, on) => {
  engine(on, () => GIT_OK, { '/repo/.git/worktrees': ['w1'], '/repo/.git/worktrees/w1/remote-agent': ['run-1'] })
  expect(await paneTexts($)).toEqual(SURFACES.map(() => 'run-1'))
})
