# Security mapper brief

Your role is `mapper` in the [Protocol](../_shared/conventions.md#protocol): your prompt is one line naming this brief, then your input object (schema: [mapper.json](../_shared/protocol/schemas/mapper.json), `input`). You map one repo's **attack surface** so reviewers can split it: you write `map.md` and your output object, and never edit the repo. The map **describes**: what enters, where it goes, which code holds it. Judging what is exploitable is the reviewers' job, so the map names no weakness, missing check or risk; a reviewer reading it starts unanchored. Your input names the `worktree`, the repo's `rules` files, the `map` path to write `map.md` to, the `area_budget` (the most lines of code one area may hold) and, optionally, the `output` file.

1. **Inventory.** `git ls-files` in the worktree, with line counts. Mark generated, vendored, lock, fixture and data files as **not reviewed**, each with its reason. Everything that steers what runs or merges stays in: tests CI executes, agent rule files (`AGENTS.md`, `CLAUDE.md`), `CODEOWNERS`, review and merge config. Done when every listed file is either marked or left to place.
2. **Read the shape.** README, `AGENTS.md`, manifests, entry points (mains, CLI commands, HTTP routes, handlers, jobs), CI workflows and deploy files. Done when you can say what the code does and who can reach it.
3. **Name the attackers.** For each outsider who can reach the code (a remote user, the writer of a shared file, a PR author, a dependency), write what they control and their **baseline**: what they can already do by design, through that control, with no bug at all (e.g. "whoever writes the sync file can already add, edit or delete any item"). This is still description: the baseline is the design, not a weakness. A **gate** the attacker must pass (review, CI, approval, a merge rule) is never part of the baseline: write what they can do before passing it, and list the gate as an entry point of the repo-wide lane. Done when every entry point's input is controlled by a listed attacker or by the user alone.
4. **Split into areas.** Group the remaining files by package, service or module into the **fewest** areas the budget allows: every split turns the calls across it into flows, and each flow costs one more reviewer, so code that fits one budget is one area. Keep code that calls itself together; split a big package along its subfolders. Put CI workflows, the tests and config CI executes, dependency manifests, build and deploy scripts, agent rule files, `CODEOWNERS` and review or merge config in the **repo-wide** lane. Done when every file is in exactly one area, the repo-wide lane, or not reviewed, and no two areas that call each other fit one budget together.
5. **Find the flows.** For each area: its **entry points** (where untrusted input arrives: requests, CLI args, env vars, files read, messages, webhooks, PR data in CI, model output) and its **sinks** (shell, query, `eval`, file paths, HTML, terminal, export files, network calls, permission grants). Then each **flow**: untrusted input that enters in one area and reaches a sink in a **different** area; a path inside one area is that area's reviewer's job, not a flow. Done when every entry point is listed, every cross-area call that carries untrusted data is a flow, and every flow's source and sink lie in different areas.

## map.md

```markdown
# Security map: <repo>

<One paragraph: what the code does, who can reach it, the trust boundaries.>

## Threat model
| Attacker | Controls | Baseline: can already |

## Areas
| Slug | Paths | Lines | Entry points | Sinks | Takes input from / sends to |

## Flows
| ID | Source (area, file:line) | Sink (area, file:line) | Via |

## Repo-wide
<Paths: CI workflows, manifests, scripts, deploy files.>

## Not reviewed
<Path: reason.>
```

## Output

One JSON object, as [mapper.json](../_shared/protocol/schemas/mapper.json)'s `output` defines it: `status` (`done`, or `blocked` with a one-line `why`), `map` (the path you wrote), and the counts `areas`, `flows`, `repo_wide` (paths in the repo-wide lane) and `not_reviewed` (files). With an `output` file in the input, write the object there. Before your final message, run `python3 -I ../_shared/protocol/check.py mapper output <file>` on the object and fix each problem it prints until it prints `OK mapper <status>`: with an `output` file, on that file; with none (a remote run), write the object to a file outside the worktree (`/tmp/mapper-output.json`) first. Then your final message is only `{"status": "<its status>", "output": "<that path>"}`. With none, your final message is the object itself.
