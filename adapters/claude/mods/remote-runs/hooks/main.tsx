// The remote-runs mod: `/remote-runs` opens a pane listing the remote-agent runs of the session's repo, read from
// `<git dir>/remote-agent/<run>/`, and a timer keeps it current. Logic sits in plain .ts files (runs.ts); this
// module only calls `$` and draws. Every `$.fs` and `$.process` call is caught and means "no data": a hook that
// throws prints a failed-hook line, and `$.process` fails on surfaces without processes (desktop, VS Code).
import { atom, read, update } from 'claude-code'
import type { EngineInterface, FsEntry, Register } from 'claude-code'

import {
  applyLiveness,
  commonDir,
  EMPTY,
  formatRow,
  gitDirs,
  livePids,
  parseRun,
  pickRows,
  type Run,
  type RunFiles,
  signature,
} from './runs.ts'

const PANE = 'remote-runs'
const TICK_MS = 1500
const runs = atom({ plugin: 'remote-runs', key: 'runs' } as const, [] as Run[], { shape: 'runs-2' })

// Per-process caches (a reload starts them empty, which only costs one full read): the common git dir per cwd,
// and each run folder's files with the sizes and mtimes they were read at. A done or failed run is not reread.
let gitDir: { cwd: string; common: string } | null = null
let seen = new Map<string, { key: string; files: RunFiles; isFinal: boolean }>()
let isTicking = false

/** The entries of `path`; none when it is missing or unreadable. */
async function entries($: EngineInterface, path: string): Promise<FsEntry[]> {
  try {
    return await $.fs.list(path)
  } catch {
    return []
  }
}

/** A file's text; null when it is missing, unreadable or over the read limit. */
async function text($: EngineInterface, path: string): Promise<string | null> {
  try {
    const got = await $.fs.read(path)
    return typeof got === 'string' ? got : null
  } catch {
    return null
  }
}

/** The repo's common git dir, from the session's directory; null without a repo, git or processes. */
async function repoGitDir($: EngineInterface): Promise<string | null> {
  try {
    const cwd = await $.session.cwd()
    if (gitDir?.cwd === cwd) return gitDir.common
    const git = await $.process.run(['git', 'rev-parse', '--path-format=absolute', '--git-common-dir'], { cwd })
    const common = commonDir(git.exitCode, git.stdout)
    gitDir = common ? { cwd, common } : null
    return common
  } catch {
    return null
  }
}

/** A run folder's run.json and progress.log, reread only when their size or mtime changed. */
async function runFiles($: EngineInterface, folder: string, id: string): Promise<RunFiles> {
  const cached = seen.get(folder)
  if (cached?.isFinal) return cached.files
  const listed = await entries($, folder)
  const file = (name: string) => listed.find(e => e.name === name && e.kind === 'file')
  const json = file('run.json')
  const log = file('progress.log')
  const key = [json?.size, json?.mtimeMs, log?.size, log?.mtimeMs].join(':')
  if (cached && cached.key === key) return cached.files
  const files: RunFiles = {
    id,
    folder,
    runJson: json ? await text($, `${folder}/run.json`) : null,
    progress: log ? await text($, `${folder}/progress.log`) : null,
    changed: Math.floor(Math.max(json?.mtimeMs ?? 0, log?.mtimeMs ?? 0) / 1000),
  }
  const { state } = parseRun(files)
  seen.set(folder, { key, files, isFinal: state === 'done' || state === 'failed' })
  return files
}

/** Every run of the repo: the folders under the common git dir's and each linked worktree's remote-agent/. */
async function scan($: EngineInterface): Promise<Run[]> {
  const common = await repoGitDir($)
  if (!common) return []
  const worktrees = (await entries($, `${common}/worktrees`)).filter(e => e.kind === 'dir').map(e => e.name)
  const found: Run[] = []
  const kept = new Map<string, { key: string; files: RunFiles; isFinal: boolean }>()
  for (const dir of gitDirs(common, worktrees)) {
    for (const e of await entries($, `${dir}/remote-agent`)) {
      if (e.kind !== 'dir') continue
      const folder = `${dir}/remote-agent/${e.name}`
      found.push(parseRun(await runFiles($, folder, e.name)))
      const cached = seen.get(folder)
      if (cached) kept.set(folder, cached)
    }
  }
  seen = kept
  return found
}

/** `ps -o pid=,lstart=` for `pids`, in one call; '' with no pid, null when ps cannot run. */
async function ps($: EngineInterface, pids: number[]): Promise<string | null> {
  if (pids.length === 0) return ''
  try {
    return (await $.process.run(['ps', '-o', 'pid=,lstart=', '-p', pids.join(',')])).stdout
  } catch {
    return null
  }
}

/** Reads every run and sets the rows only when they would draw differently. Never throws. */
async function tick($: EngineInterface): Promise<void> {
  if (isTicking) return
  isTicking = true
  try {
    const now = Math.floor((await $.clock.now()) / 1000)
    const found = await scan($)
    const rows = pickRows(applyLiveness(found, await ps($, livePids(found))), now)
    if (signature(rows) !== signature(await read($, runs))) await update($, runs, () => rows)
  } catch {
    // no data this tick; the next one tries again
  } finally {
    isTicking = false
  }
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    await $.command.register({ name: 'remote-runs', description: 'Show the remote-agent runs of this repo in a pane' })
    $.clock.every(TICK_MS, () => {
      void tick($)
    })
    return next(e)
  })

  on('command.run', { command: 'remote-runs' }, async $ => {
    await tick($)
    const { isPlaced } = await $.ui.open({ id: PANE, title: 'Remote runs', focus: true, closeOnEscape: true })
    return { text: isPlaced ? 'Remote runs pane opened.' : 'Remote runs pane opens once the terminal is wider.' }
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const { Box, Text, Button } = $.ui.resolve(e)
    const list = await read($, runs)
    return (
      <Box flexDirection="column">
        {list.length === 0 && <Text dimColor>{EMPTY}</Text>}
        {list.map((run, i) => {
          const row = formatRow(run)
          return (
            <Box flexDirection="column">
              {/* Ticket 02 opens the run's log on a press. */}
              <Button key={`run:${run.id}`} plain {...(i < 9 ? { hotkey: String(i + 1) } : {})} onPress={() => {}}>
                {row.label} <Text dimColor>{row.status}</Text>
              </Button>
              {row.detail !== '' && <Text dimColor wrap="truncate-end">{`   ${row.detail}`}</Text>}
            </Box>
          )
        })}
      </Box>
    )
  })
}
