// The remote-runs mod: `/remote-runs` opens a pane listing the remote-agent runs of the session's repo, read from
// `<git dir>/remote-agent/<run>/`, and a timer keeps it current; pressing a run shows its log, which the timer reads
// on into the `log` atom, and the pane draws a window of. Logic sits in plain .ts files (runs.ts, transcript.ts);
// this module only calls `$` and draws. Every `$.fs` and `$.process` call is caught and means "no data": a hook that
// throws prints a failed-hook line, and `$.process` fails on surfaces without processes (desktop, VS Code).
import { atom, read, update } from 'claude-code'
import type { EngineInterface, FsEntry, Register } from 'claude-code'

import {
  applyLiveness,
  commonDir,
  EMPTY,
  endedRuns,
  formatRow,
  gitDirs,
  livePids,
  markNotified,
  notifyText,
  parseRun,
  pickRows,
  type Run,
  type RunFiles,
  signature,
  statusText,
} from './runs.ts'
import type { Log, Ticker, View } from './state.d.ts'
import {
  byteSlice,
  EARLIER,
  NO_LOG,
  oneLine,
  readChunk,
  readFrom,
  scrollLog,
  WAITING,
  windowLines,
} from './transcript.ts'

const PANE = 'remote-runs'
const TICK_MS = 1500
const runs = atom({ plugin: 'remote-runs', key: 'runs' } as const, [] as Run[], { shape: 'runs-2' })
const ticker = atom({ plugin: 'remote-runs', key: 'ticker' } as const, { seeded: [], status: null } as Ticker, {
  shape: 'ticker-1',
})
const view = atom({ plugin: 'remote-runs', key: 'view' } as const, { runId: null } as View, { shape: 'view-1' })
const log = atom({ plugin: 'remote-runs', key: 'log' } as const, NO_LOG, { shape: 'log-1' })
// A transcript under this size is read with $.fs.read (which refuses one over 4 MiB); a bigger one with tail.
const FS_READ_MAX = 3 << 20
// The pane's rows above the log: Back and the run, then the transcript path; and the note when earlier events
// are not shown.
const HEADER_ROWS = 2
const logRows = (bodyRows: number, l: Log) => Math.max(1, bodyRows - HEADER_ROWS - (l.truncated ? 1 : 0))
// $.store key per common git dir: the folders of its runs already notified (or marked so), kept across processes,
// newest last. Past NOTIFIED_MAX, only folders gone from disk are dropped.
const notifiedKey = (common: string) => `notified:${common}`
const NOTIFIED_MAX = 1000

// Per-process caches (a reload starts them empty, which only costs one full read): the common git dir per cwd,
// and each run folder's files with the sizes and mtimes they were read at. A done or failed run is not reread.
let gitDir: { cwd: string; common: string } | null = null
let seen = new Map<string, { key: string; files: RunFiles; isFinal: boolean }>()
let isTicking = false
// The open log's tool call ids, so a result names its tool across reads, and the transcript size last read, so an
// unchanged file is not read again; and the queue its reads go through.
let logCache = { runId: null as string | null, names: new Map<string, string>(), size: -1 }
let logQueue: Promise<void> = Promise.resolve()

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

/**
 * Every run of the repo: the folders under the common git dir's and each linked worktree's remote-agent/, and
 * that dir; null without one.
 */
async function scan($: EngineInterface): Promise<{ common: string; found: Run[] } | null> {
  const common = await repoGitDir($)
  if (!common) return null
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
  return { common, found }
}

/**
 * The transcript's text from byte `from`, at most `length` bytes of it; null when it cannot be read. A file under
 * FS_READ_MAX is read whole, one over it through `tail` (`$.fs.read` refuses a file over 4 MiB).
 */
async function chunk($: EngineInterface, path: string, size: number, from: number, length: number): Promise<string | null> {
  if (size < FS_READ_MAX) {
    const whole = await text($, path)
    return whole === null ? null : byteSlice(whole, from, length)
  }
  try {
    const argv = ['sh', '-c', 'tail -c "+$1" -- "$2" | head -c "$3"', 'sh', String(from + 1), path, String(length)]
    const got = await $.process.run(argv)
    return got.exitCode === 0 ? got.stdout : null
  } catch {
    return null
  }
}

/**
 * Reads the open run's new transcript bytes into the log atom; a run other than the log's (another opened, or
 * Back) starts it again. Never throws.
 */
async function readLog($: EngineInterface): Promise<void> {
  try {
    const { runId } = await read($, view)
    const was = await read($, log)
    let l = runId === was.runId ? was : { ...NO_LOG, runId }
    if (l !== was || logCache.runId !== runId) logCache = { runId, names: new Map(), size: -1 }
    const open = runId === null ? undefined : (await read($, runs)).find(r => r.id === runId)
    if (open) {
      let size: number | null = null
      try {
        size = (await $.fs.stat(open.transcript)).size
      } catch {
        // no transcript yet
      }
      if (size !== null && size !== l.offset && size !== logCache.size) {
        const at = readFrom(l, size)
        if (at.isReset) {
          l = { ...NO_LOG, runId }
          logCache.names.clear()
        }
        const got = await chunk($, open.transcript, size, at.from, at.length)
        if (got !== null) {
          l = readChunk(l, got, { ...at, isFull: size - at.from >= at.length }, logCache.names)
          logCache.size = size
        }
      }
    }
    if (l !== was) await update($, log, () => l)
  } catch {
    // no new lines this time
  }
}

/** readLog, one at a time: the timer and a press may both ask. */
function followLog($: EngineInterface): Promise<void> {
  logQueue = logQueue.then(() => readLog($))
  return logQueue
}

/**
 * `ps -o pid=,lstart=` for `pids`, in one call, in the C locale as remote-agent wrote pid_start (lstart follows the
 * locale on macOS); '' with no pid, null when ps cannot run.
 */
async function ps($: EngineInterface, pids: number[]): Promise<string | null> {
  if (pids.length === 0) return ''
  try {
    return (await $.process.run(['env', 'LC_ALL=C', 'ps', '-o', 'pid=,lstart=', '-p', pids.join(',')])).stdout
  } catch {
    return null
  }
}

/**
 * `judged` (`found` after applyLiveness) with each run it calls stopped read again: one that wrote done or failed
 * between the scan and `ps` ended on its own, so it takes its run.json's state rather than stopped.
 */
async function recheckStopped($: EngineInterface, judged: Run[]): Promise<Run[]> {
  const out: Run[] = []
  for (const r of judged) {
    if (r.state !== 'stopped') {
      out.push(r)
      continue
    }
    const again = parseRun({ id: r.id, folder: r.folder, runJson: await text($, `${r.folder}/run.json`), progress: null, changed: r.changed })
    out.push(again.state === 'done' || again.state === 'failed' ? { ...again, step: r.step } : r)
  }
  return out
}

/**
 * Notifies each run that ended since the last tick, once: the notified folders live in $.store, so a reload or a
 * new process does not notify again. The first tick of a process for a git dir marks every finished run without
 * notifying. A run is marked even when no channel sent it, so a disabled channel is not tried every tick.
 */
async function notifyEnded($: EngineInterface, common: string, all: readonly Run[], t: Ticker): Promise<Ticker> {
  let stored: unknown
  try {
    stored = await $.store.get(notifiedKey(common))
  } catch {
    return t // no store, no memory: notifying now would repeat every tick
  }
  const notified = Array.isArray(stored) ? stored.filter((f): f is string => typeof f === 'string') : []
  const ended = endedRuns(all, new Set(notified))
  const isSeeded = t.seeded.includes(common)
  if (ended.length > 0) {
    for (const r of isSeeded ? ended : []) {
      try {
        await $.ui.notify(notifyText(r), { title: 'remote run' })
      } catch {
        // a refused or failed write: marked all the same
      }
    }
    const present = new Set(all.map(r => r.folder))
    await $.store.set(notifiedKey(common), markNotified(notified, ended, present, NOTIFIED_MAX))
  }
  return isSeeded ? t : { ...t, seeded: [...t.seeded, common] }
}

/**
 * Reads every run, sets the rows only when they would draw differently, the status line only when its text
 * changed, and notifies the runs that ended. Runs from the timer, so all of it works with the pane closed. Never
 * throws.
 */
async function tick($: EngineInterface): Promise<void> {
  if (isTicking) return
  isTicking = true
  try {
    const now = Math.floor((await $.clock.now()) / 1000)
    const got = await scan($)
    const found = got?.found ?? []
    const all = await recheckStopped($, applyLiveness(found, await ps($, livePids(found))))
    const rows = pickRows(all, now)
    if (signature(rows) !== signature(await read($, runs))) await update($, runs, () => rows)

    const was = await read($, ticker)
    let t = was
    const status = statusText(all, now)
    if ((status ?? null) !== t.status) {
      $.ui.status(status)
      t = { ...t, status: status ?? null }
    }
    if (got) t = await notifyEnded($, got.common, all, t)
    if (t !== was) await update($, ticker, () => t)
    await followLog($)
  } catch {
    // no data this tick; the next one tries again
  } finally {
    isTicking = false
  }
}

/** Shows run `runId`'s log, read at once rather than on the next tick. */
async function openLog($: EngineInterface, runId: string): Promise<void> {
  await update($, view, () => ({ runId }))
  await followLog($)
}

/** Back to the list; the log starts again when a run is next opened. */
async function back($: EngineInterface): Promise<void> {
  await update($, view, () => ({ runId: null }))
  await update($, log, () => NO_LOG)
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

  // The log draws its own window, exactly its rows: the person's scroll moves it, and the list scrolls as usual.
  on('ui.scroll', { component: 'Pane', requestId: PANE }, async ($, e, next) => {
    const { runId } = await read($, view)
    if (runId === null) return next(e)
    const l = await read($, log)
    if (l.runId === runId) await update($, log, () => scrollLog(l, e.by, logRows(e.bodyRows, l)))
    return {}
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const { Box, Text, Button } = $.ui.resolve(e)
    const list = await read($, runs)
    const { runId } = await read($, view)
    // The open run's log; a run gone from the list (its folder removed) draws the list again.
    const open = runId === null ? undefined : list.find(r => r.id === runId)
    if (open) {
      const row = formatRow(open)
      const l = await read($, log)
      const shown = l.runId === open.id ? l : { ...NO_LOG, runId: open.id }
      const rows = logRows(e.props.scroll.bodyRows, shown)
      return (
        <Box flexDirection="column">
          <Box>
            <Button key="back" hotkey="b" onPress={() => back($)}>
              Back
            </Button>
            <Button key="end" hotkey="e" onPress={() => update($, log, l => ({ ...l, follow: true }))}>
              End
            </Button>
            <Text wrap="truncate-end">
              {` ${oneLine(row.label)} `}
              <Text dimColor>{row.status}</Text>
            </Text>
          </Box>
          <Text dimColor wrap="truncate-end">
            {oneLine(open.transcript, 1000)}
          </Text>
          {shown.truncated && <Text dimColor wrap="truncate-end">{EARLIER}</Text>}
          {shown.lines.length === 0 && <Text dimColor>{WAITING}</Text>}
          {windowLines(shown.lines, shown.follow, shown.top, rows).map(line => (
            <Text wrap="truncate-end">{line}</Text>
          ))}
        </Box>
      )
    }
    return (
      <Box flexDirection="column">
        {list.length === 0 && <Text dimColor>{EMPTY}</Text>}
        {list.map((run, i) => {
          const row = formatRow(run)
          return (
            <Box flexDirection="column">
              <Button
                key={`run:${run.id}`}
                plain
                {...(i < 9 ? { hotkey: String(i + 1) } : {})}
                onPress={() => openLog($, run.id)}
              >
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
