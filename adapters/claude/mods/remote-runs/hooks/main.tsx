// The remote-runs mod: `/remote-runs` opens a pane listing the remote-agent runs of the session's repo,
// read from `<git dir>/remote-agent/<run>/`. Logic sits in plain .ts files (runs.ts); this module only calls `$`.
// Every `$.fs` and `$.process` call is caught and means "no data": a hook that throws prints a failed-hook line,
// and `$.process` fails on surfaces without processes (desktop, VS Code).
import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import { commonDir, EMPTY, gitDirs, type Run } from './runs.ts'

const PANE = 'remote-runs'
const runs = atom({ plugin: 'remote-runs', key: 'runs' } as const, [] as Run[], { shape: 'runs-1' })

/** The names of the folders in `path`; none when it is missing or unreadable. */
async function folders($: EngineInterface, path: string): Promise<string[]> {
  try {
    return (await $.fs.list(path)).filter(e => e.kind === 'dir').map(e => e.name)
  } catch {
    return []
  }
}

/** The repo's common git dir, from the session's directory; null without a repo, git or processes. */
async function repoGitDir($: EngineInterface): Promise<string | null> {
  try {
    const cwd = await $.session.cwd()
    const git = await $.process.run(['git', 'rev-parse', '--path-format=absolute', '--git-common-dir'], { cwd })
    return commonDir(git.exitCode, git.stdout)
  } catch {
    return null
  }
}

/** Every run folder of the repo: under the common git dir and each linked worktree's. */
async function scan($: EngineInterface): Promise<Run[]> {
  const common = await repoGitDir($)
  if (!common) return []
  const found: Run[] = []
  for (const dir of gitDirs(common, await folders($, `${common}/worktrees`))) {
    for (const id of await folders($, `${dir}/remote-agent`)) found.push({ id, folder: `${dir}/remote-agent/${id}` })
  }
  return found
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    await $.command.register({ name: 'remote-runs', description: 'Show the remote-agent runs of this repo in a pane' })
    return next(e)
  })

  on('command.run', { command: 'remote-runs' }, async $ => {
    const found = await scan($)
    await update($, runs, () => found)
    const { isPlaced } = await $.ui.open({ id: PANE, title: 'Remote runs', focus: true, closeOnEscape: true })
    return { text: isPlaced ? 'Remote runs pane opened.' : 'Remote runs pane opens once the terminal is wider.' }
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const { Box, Text } = $.ui.resolve(e)
    const list = await read($, runs)
    return (
      <Box flexDirection="column">
        {list.length === 0 && <Text dimColor>{EMPTY}</Text>}
        {list.map(run => (
          <Text wrap="truncate-end">{run.id}</Text>
        ))}
      </Box>
    )
  })
}
