# agent-flow

A Claude Code mod: `/agent-flow` opens a pane that draws this session's agents as a live org chart. `main` sits at the top, centred; each subagent hangs in a box under the agent that started it, joined by a connector. The pane opens only by the command, never by itself, so it shows at any terminal width.

It only observes. Every hook passes its call on with `next(e)`, unchanged, and records what happened; each hook that wraps a call (`agent.spawn`, `tool.call`, `tool.check`) has a `.catch` that passes the call on, so a failure in the mod can cost the chart an event but never fails, delays or changes a call. It makes no network requests; the only processes it runs are `ps` (is a remote run's `remote-agent` still alive) and `tail` (a remote transcript over 3 MiB), both only while a remote run of this session is live, and where a surface has no processes it does without them.

## What a box shows

Each box (24 columns) shows the agent's status glyph and type, its task (the Agent tool's description), its model (`opus 5.5`, from the resolved id; Bedrock and Vertex ids read the same) with the time it has run, and one last line:

- **running**: a spinner in the model's colour, and the tool call running now (`⎿ Bash npm test`, the input field that says what it does, control characters dropped), or `⎿ thinking` between calls;
- **needs you** (`◐`, warning colour): a permission check pending at least 1 s. Quicker checks, which the auto-mode classifier settles, never show;
- **done** (`✓`, dim): its steps (tool calls) and time;
- **failed** (`✗`, error colour): why: `stopped` (aborted), `refusal` or `error`, from its turn's end, or `error` when the Agent call that started it errored.

`main` shows `waiting on agents`, `thinking` or `idle`, its model (learned from the first spawn) and the turn count.

**Context**: each box's bottom border carries a gauge of the context its agent has used against its model's window, `╰─ ▰▰▱▱▱▱ 42k/200k ─╯`, coloured by how full: under half `success`, to 80% `warning`, past it `error`. Subagents' readings come from `$.agent.list()` (`tokenCount`, its latest request's input and its output, over `contextWindowSize`), main's from `$.session.usage()`; both are taken once a second while an agent runs or ended in the last 10 s, so the gauge may trail the engine's own reading (refreshed about every 5 s). A finished box keeps its last reading, dim. A squad's border shows its members' average and its fullest member's gauge (`ctx avg 34% · max ▰▰▰▰▱▱ 61%`): the average says how full the squad runs, the max finds the one member close to its limit, which an average hides. The Agents list shows the figure on each row, and every squad member shows its own, both in the squad expanded under the chart and in a group opened in the list. A box with no reading yet has a plain border.

**Cost**: what `/cost` credited each agent, in dollars, from `$.agent.list()` (`cost.usd`, its own requests only, not its subagents'), read with the context; main's is the session's total from `$.session.usage()`. It is an estimate, not the bill. A box shows it in its top border (` $0.12 `), a squad its members' sum. The Agents list leaves it out, to fit a side pane; an agent's log shows it in its header card and its end line (`✓ done in 0:42 · 17 steps · $0.12`). A remote run shows the cost from its `result.json`. Nothing shows before the first reading.

Colours: by default (option `colors` `plan`) the plan's fixed dark palette: opus `#e0835f`, sonnet `#7fa8f2`, haiku `#5cc9a7`, fable `#c39cf2` for the model; `#e8b34f` needs you, `#6cc58a` done, `#f0727a` failed; faint `#3c3f49` for rules and `⎿` marks; the selected tab or filter filled `#2a2c34`. Option `colors` `theme` draws the same parts in the theme's keys instead (`claude`, `suggestion`, `merged`, `warning`, `success`, `error`, `inactive`, the primary button), so light and colour-blind themes keep working. The model's family colours a running agent; the glyph and colour of its status say how it is.

## When boxes come and go

- A new box enters over 0.6 s: its connector grows from the parent, then the border draws, then the content.
- A running box stays. A finished one lingers (option `linger_s`, 30 s), then dims with a dotted border, shrinks to a one-line chip (`╶ ✓ Explore 0:42 ╴`), and goes, over 1 s.
- A failed box stays until the next turn starts, then leaves like a finished one once it has lingered. A box with a child still shown stays until that child has gone.
- A new turn never cuts a linger short (a background agent's notification starts a turn too); the header then says what the last turn came to (`last turn: ✓ 4 ✗ 1`).
- `main` never leaves and never moves: everything is placed relative to it.
- When the layout changes, every box glides from where it is to its new spot on one shared 320 ms ease-out, so a row moves as a whole; a pass then keeps at least 2 columns between neighbours in every row, so no two boxes ever touch.

## Remote runs

A run of `remote-agent` (the `remote` group) shows as a box of its own, `☁ <name>`, under the agent whose Bash call started it, with the subagents it ran inside the sandbox as boxes under it. Its box shows its setup phase while the sandbox starts (`◌ claiming…`, `sending…`, `booting…`), then its current tool call; once done, its cost and the files it changed (`✓ $0.25 · 3 files`), with its time on the model line; a failed one, why (`exit 2`, `stopped`, `lost sandbox`, `capped`).

- **Which runs**: on the chart, those whose `run.json` names this session as its caller (`caller.session`, written by remote-agent from `CLAUDE_CODE_SESSION_ID`). The repo's other runs (another session's, or one that recorded none), the 20 newest with live ones first, are listed in the Agents tab only, under `other sessions`, with their logs; never on the chart. The run folders are found with file reads only: the `.git` above the session's folder, through a worktree's `gitdir:` and `commondir`, to `<common git dir>/remote-agent/<run>/` and each linked worktree's.
- **Under which agent**: each Bash call that starts `remote-agent` (not its `check`, `result` and other subcommands) is recorded with its agent, start, `--name` and description, which becomes the run's task. A run goes under the call whose output printed its `transcript:` path; else the call that gave its `--name`; else the earliest call that began up to 2 minutes before it and still ran then or went to the background. With none, it hangs under main, and its log says no call was found.
- **Alive**: a starting or running run whose pid is gone, or was reused (`ps` with the pid's start time), is stopped; where processes cannot run, one whose files went quiet for 5 minutes.
- **When**: the folders are read every 1.5 s while a `remote-agent` call or any run is live or the pane was drawn in the last 2 s, else every 15 s (and once after the mod loads); each folder's files again only when they changed, and a finished run's not at all once its transcript and `result.json` were read.
- **Its log**: its setup from `progress.log`, then its transcript (tool calls, results, errors, and the agent's last message as it wrote it, less any JSON object in it: the outcome a skill's brief asks the run to end with is for that skill, and the end line already says how the run went), with `summary.md` under `── summary ──` only when the transcript has no last message (it repeats that message). The transcript's own `✓ done in 59s` line gives way to the log's end line, which times the whole run; a sandbox subagent's log, its own events. `session.jsonl` is never read.
- **Resume**: a finished run whose `session.jsonl` came back ends its log with `remote-agent resume <run> <worktree>` (the worktree shell-quoted) and the warning that the sandbox wrote that session; one without says it cannot be resumed. `p` copies the transcript's path, `r` the resume command.
- **Ended**: each run of the repo that ends (done, failed or stopped) raises one notification, `<name> done`, `<name> failed (exit 2)`, `<name> stopped`. The notified run folders are kept per repo in the mod's store (`notified:<common git dir>`, at most 1,000, those gone from disk dropped first), and the first read of a process marks the runs already over without notifying, so a reload never repeats one.

## Many agents

- **Squads**: 4 or more siblings with the same parent and type (option `squad_min`) fold into one squad box, 48 columns wide however many: the type and count, the model, one cell per agent in launch order (its status glyph; a gap every 5; past 36, `+N`), a progress bar (done, running, waiting, left) with counts. `x` (or the Expand button) lists the members under the chart, one line each, in up to 3 columns.
- **Folding**: when a row is wider than the pane, finished siblings fold into one `✓ N done` box, the parent with the most children first. Running and failed boxes never fold.
- **Too narrow**: when even folded the chart does not fit, the pane lists its running agents instead, as the Agents tab draws them, and says how many columns the chart needs. It goes back to the chart only once the chart has fitted with 4 columns to spare for 2 s, so a chart on the edge never flickers between the two.

## The Agents tab

`a` (or the Agents tab) lists every agent of this session the mod has seen, so an agent stays findable after its box left the chart, in three sections: `this turn`, the agents running or ended since the prompt that started this turn (running ones first, newest first; then the finished by when they ended); `earlier this session`; `other sessions`, the repo's remote runs other sessions started; and `── older ──`, those that ended over 30 minutes ago, packed and dim. A remote run found after it ended, long over, goes by when it ended, not under this turn. Each row is a table row: the number to open it, the status glyph, the type, the status and time (`running 0:42`, `needs permission 0:03`, `done 0:42 · 3m ago`, a remote run's setup phase), the model, a context gauge with its share (`▰▰▱▱ 42% ctx`) and the task (a remote run's says what it changed: `☁ remote · no changes`, `3 files changed`). The agents an agent started sit under its row, indented with `↳`, one line each with the same columns, a level deeper for each generation (a remote run's sandbox subagents too); an agent whose parent the filter leaves out gets a row of its own, with `↳` before its type. A running or failed agent has its step or why under it; a blank line follows a row with something under it, and finished rows sit packed. `g` goes back to the chart.

- **Filters**: `l` All, `r` Running, `n` Needs you (waiting for a permission, or failed this turn), `f` Failed, `d` Done, each with its count.
- **Squad groups**: under All, 4 or more siblings of one type (option `squad_min`) are one collapsed row with a progress bar and their counts (`▸ epic-implementer ×24  ███▓▓▓░░  18/24 done · 3 running · ◐ 1 need you · ✗ 1`), still listing the members that need you, one compact line each, then `… N more · click to show`; pressing it opens it to every member. A click on a squad box in the chart opens its group here.

## A log

`1`–`9` (or a click) on a row, or a click on a box or squad cell in the chart, opens that agent's log. It reads the session's own record, `$.session.messages({ agentId })`, which keeps a finished agent's messages:

- a header card: Back, the agent, a state badge (`RUNNING`, `WAITING`, `DONE`, `FAILED`, or a remote run's setup phase), its model and time; under it its task, its parent, steps and context (a remote run: its run id, its caller, turns and cost);
- a rule with the agent and its model, `task: ` its task; its own text, less any JSON object in it (an outcome a skill's brief asks for, which the end line already says); `▸ Bash npm test`, each tool call; `⎿ ` the first line of each result, with how many more lines it held, an error's in red;
- `▸ Agent fork · try bash 3.2` and under it `⎿ open its log →`: a call that started an agent links to that agent's log, `1`–`9` in the order shown; a call still running, `⎿ ● running…` (`⎿ ◐ waiting for your permission` while it waits);
- once it finished, how it ended: ` ✓ done  in 0:42 · 12 steps`, or ` ✗ failed  (error) after 0:42`; a remote run's adds its cost, turns and changes, and that its `session.jsonl` is kept, never read;
- under a rule: `● Following live` (or `■ End of log`), back, copy log, open transcript (a remote run: copy path, copy resume).

Secret-shaped strings (API keys, GitHub and Slack tokens, AWS keys, JWTs, `Bearer` values, URL passwords, `*_TOKEN=` values) are masked, and control characters dropped.

**Back** (`b`) goes the way the log was opened: from a link, to the log the link was in (`← epic-implementer`); else to the tab it came from (`← Chart`, `← Agents`). A tab, a filter or a new prompt forgets that way back. Esc closes the pane.

The log follows the end while the agent runs (read again every second, and once more in the 5 s after it ends), and `↑↓` scroll it; `e` jumps back to the end. `c` copies the log as drawn; `v` opens the agent's whole transcript in the main view (a remote run's has `p` and `r` instead). It keeps the last 2,000 lines, and a first read asks for the newest 400 messages, saying so when older ones were left out. The list holds as many agents as the graph (100).

## The rest of the pane

Over the chart: the counts of this turn (`3 running · 1 need you · 4 done · 1 failed`) and the **needs you** strip, naming every agent waiting for you or failed this turn (two, then `+N more`), wherever it sits, never hidden in a squad. Above it, the Chart and Agents tabs (`g`, `a`). Under it: the Expand button, with a hotkey (quick repeated clicks select text in Claude Code and are lost, keys never are), and what the keys do.

The band, on your status line, at any width and with the pane closed: up to 4 running agents by task, each with a spinner in its model's colour (`⠹ fix login  ·  ⠼ run tests`), past 4 their count (`⠹ 18 running`), then `◐ 1 needs you · ☁ 2 remote · ✗ 1 failed`, where `☁` counts the repo's live remote runs, this session's and others', and with two or more agents this turn a bar of how many ended (`▰▰▰▱▱▱▱▱ 5/26`, red once one failed); nothing when no agent runs, waits or failed this turn. The mod writes it, when it changes, to `<session scratchpad>/agent-flow/band.txt`, one line per spinner frame; `statusline.sh` prints the line of the current second. To show it, add its output as a line of your status line command, which has read its JSON payload into `$input`:

```bash
~/.claude/skills/agent-flow/statusline.sh "$(jq -r '.scratchpad_dir // ""' <<<"$input")"
```

and set `"refreshInterval": 1` under `statusLine` in `~/.claude/settings.json`, so the spinners step once a second. The mod clears the status line earlier versions set (`$.ui.status`) at session start.

A timer runs every 125 ms. It redraws the pane only while something on it moves (an agent running, a box entering, gliding or leaving, a box lingering towards its leave) and the pane was drawn in the last 2 s, so a closed or still pane costs nothing. Spinners, the dot that runs along a live connector, and the glide are terminal-only; on other surfaces, or with option `motion` off, the chart is still.

The graph lives in `$.state`, so a hot reload keeps it; at most 100 agents, the oldest finished ones dropped first. Agents from before the mod loaded are not shown.

## Options

| Option | Default | Meaning |
|---|---|---|
| `linger_s` | 30 | Seconds a finished agent's box stays before it leaves |
| `squad_min` | 4 | Siblings of one agent type that fold into one squad box |
| `motion` | `on` | `on`: spinners, moving dots and gliding boxes; `off`: a still chart |
| `colors` | `plan` | `plan`: the design's fixed dark palette; `theme`: your Claude Code theme's colours |

These are the mod's own plugin options (`/config`), not dotagents Settings, so the mod works on a machine without dotagents.

`./install.sh claude agent-flow` links this folder to `~/.claude/skills/agent-flow`, where Claude Code loads it as a plugin and reloads it on every save.

## Files

- `hooks/main.tsx`: the hooks module. Records events, runs the timer, writes the band for the status line, draws the pane.
- `hooks/flow.ts`: the agent graph, built from events by one pure reducer; summaries, model labels and colours, what the band says and its status line text.
- `statusline.sh`: prints the band's line of the current second for a status line command.
- `hooks/lifecycle.ts`: when a box enters, lingers and leaves.
- `hooks/remote.ts`: remote runs: finding the run folders, `run.json`, `result.json` and transcripts read into box fields, liveness, linking a run to its Bash call, a remote log. Resuming a run and notifying the ended ones too. `parseRun`, the liveness rules, resume and notifications come from the remote-runs mod this one replaced.
- `hooks/transcript.ts`: a remote transcript's reader and renderer, with its masking, moved from the remote-runs mod this one replaced.
- `hooks/log.ts`: an agent's log (messages to lines, masking, links, the scrolled window), the Agents list (sections, filters, squad groups) and the way Back goes.
- `hooks/layout.ts`: the chart on a character grid (squads, folding, the shared tween, the minimum gap, connectors), the strips, the expanded squad and the narrow fallback.
- `hooks/state.d.ts`: the state contract (`plugin.json` `types`) and the shared types. Change an atom's `shape` tag when its fields change.
- `hooks/*.unit.ts`: the logic under `node --test`, including a frame scan that plays two sessions (five agents with a fork; a 24-agent fan-out) frame by frame and fails if two boxes ever touch.
- `hooks/main.test.tsx`: engine tests under `claude plugin test`, with the engine's answers faked.

## Tests

```sh
bash tests/mod.test.sh                                     # every mod: the below
node --test adapters/claude/mods/agent-flow/hooks/*.unit.ts
claude plugin validate adapters/claude/mods/agent-flow
claude plugin test adapters/claude/mods/agent-flow
```
