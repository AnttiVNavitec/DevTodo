# DevTodo — Claude context

## Run
`serve.bat` → `http://localhost:8080` (Python server in `server.py`)
Jira/GitLab calls are proxied through the server: `/proxy/jira/*`, `/proxy/gitlab/*`
Git worktree state comes from the server too: `/worktrees`, `/worktrees/scan`, `/activity`
Tool launching: `POST /worktree/open` `{tool, path}`

## File layout
- `index.html` — all HTML + all JS in one IIFE (no build step, no modules)
- `styles.css` — all CSS, separate file
- `server.py` — local proxy server, routing only for anything non-trivial
- `fetch_mr_comments.py` — helper called by server.py
- `jira_downloader.py` — helper called by server.py
- `worktrees.py` — git worktree discovery (shells out to git, no repo-specific knowledge)
- `activity.py` — background poller that logs worktree activity signals to `data/`
- `spawn.py` — launches external tools (Explorer, Git Bash, VS Code, Claude…) in a worktree
- `PLAN-worktrees.md` — phased plan for the worktree / auto-time-tracking work
- `data/` — gitignored. Activity log + remembered repo paths. **Real work data: never commit.**

**Do not split the JS into modules.** The JS is a single IIFE; all state is shared via closure. Splitting would require globals or ES modules and isn't worth it at the current size. Python *is* split into modules — `server.py` stays a router.

## JS sections in index.html
Grep for `// ── <name>` to jump directly. Sections in order:

| Section | What's there |
|---|---|
| `Storage keys` | All `const KEY_*` and `const POMO_*` / `BREAK_*` constants |
| `Default settings` | `DEFAULT` object shape |
| `State` | All `let` state vars — read this to understand what exists |
| `Helpers` | `loadSettings`, `saveTodos`, `deepClone`, `esc`, `$id`, week number |
| `Time tracking storage` | load/save for entries, active entry, suggestions, pruning, pomo/ctx helpers |
| `Pomodoro timer` | `pomoStart`, `pomoInterrupt`, `pomoSkipBreak`, `buildDayStats`, `renderPomo` |
| `Tracker bar` | `clockIn`, `clockOut`, `checkAutoClockout`, `renderTracker` |
| `Other work modal` | `openOtherWork`, `submitOtherWork` |
| `Time report modal` | `openTimeReport`, `renderTimeReport`, `renderLogView`, `renderSummaryView` |
| `Jira` | `cacheJiraSummaries`, `fetchJiraSummary`, `jiraFetch`, `fetchMyJiraIssues`, `fetchSupportTickets`, `fetchEpicChildren`, `createEpicIssue`, `buildJiraItem`, `loadJira`, `loadEpicPanel` |
| `Jira sorting` | `priorityOrder`, `jiraStatusTier`, `issueInActiveSprint`, sort comparators |
| `GitLab` | `fetchGitLabMRs`, `buildGitLabItem`, `downloadMrComments`, `loadGitLab` |
| `MR ranking` | `mrRank` |
| `Local Todos` | `renderTodos`, `addTodo` |
| `Worktrees` | `ticketKeyOf`, `fmtAgo`, `worktreeLabel`/`worktreeLabelSync`, `isWorktreeActive`, `buildWorktreeItem`, `renderWorktrees`, `loadWorktrees`, `scanForRepos`, `parseRoots` |
| `Contextual actions` | `ACTIONS` registry, `openWorktreeTool`, `trackedContext`, `worktreeContext`, `findTrackedWorktree`, `renderContextActions` |
| `Settings Modal` | `openSettings`, `closeSettings`, `collectSettings` |
| `Event wiring` | All `addEventListener` calls |
| `Init` | Startup sequence |

## CSS sections in styles.css
Same pattern, grep for `/* ── <name>`. Key sections: `Pomodoro bar`, `Tracker bar`, `Time report`, `Summary view`, `Day stats in time report`, `Worktrees`.

## Key shared state vars
```
settings          loaded from localStorage, shape: { jira, gitlab, pomo, worktrees }
activeEntry       null | { id, label, type, startedAt, endedAt:null } — the running timer
timeEntries       completed entries array
timeSuggestions   autocomplete history for "Other work"
pomoState         null | { phase:'work'|'break', startedAt, durationMs }
pomoCycles        [{ date:'YYYY-MM-DD', type:'completed'|'interrupted' }]
ctxSwitches       [{ date:'YYYY-MM-DD' }] — one record per context switch
reportDate        YYYY-MM-DD string driving the time report modal
reportTab         'log' | 'summary'
summarySelection  Set<label> of checked rows in summary view
jiraSummaries     Map<issueKey, summary> — warmed by loadJira/loadEpicPanel
worktreeData      last /worktrees response; renderWorktrees() reads it without refetching
```

## Storage keys (all `devtodo_*`)
`settings`, `todos`, `show_unassigned`, `time_entries`, `time_active`, `time_suggestions`, `epic`, `pomo_state`, `pomo_cycles`, `ctx_switches`

## Activity logging (`activity.py`)
Runs on a daemon thread started by `server.py`, independent of the browser, so it keeps
collecting with no tab open. Phase 5 of `PLAN-worktrees.md` consumes it; the history
cannot be backfilled, which is why it collects before anything reads it.

- Polls every 60s. Appends to `data/activity.jsonl` **only when something changed**, plus a
  heartbeat every 15 min so "nothing happened" is distinguishable from "poller was down".
- Records carry a `why` list: `start`, `branch`, `commit`, `files`, `claude`, `heartbeat`,
  `error` (a repo/worktree became unreadable), `gone` (stopped being reported).
  `error`/`gone` exist so absence of records never has to mean "we couldn't look".
- `files` fingerprints the staged/unstaged/untracked/conflicted **breakdown**, not just the
  total — otherwise a bare `git add` would go unnoticed.
- Claude activity comes from the newest `*.jsonl` mtime under
  `~/.claude/projects/<mangled-path>/`, where mangling replaces `:` `/` `\` with `-` and
  lowercases the drive letter. Each transcript dir is attributed to the **longest** matching
  worktree, or `<repo>-worktree` sessions would be credited to `<repo>`. Undocumented
  format — every failure path degrades to "no signal".
- The repo list lives in browser localStorage, so the server remembers whatever roots it was
  last asked about in `data/roots.json` and polls those. An empty list never overwrites it.
- Retention is 14 days, pruned once a day, matching the dashboard's time entries.

## Contextual actions
One registry (`ACTIONS`) drives every action button, rendered onto two surfaces:
the **tracker bar** (acting on whatever is being tracked) and each **worktree row**
(acting on that worktree). Add an action once and pick its surfaces; don't hand-write
buttons into either place.

```
{ id, icon, label, title, surfaces: ['tracker'|'worktree'], applies(ctx), run(ctx) }
```

- `applies(ctx)` → `true` show enabled · `'reason'` show disabled with the reason as
  tooltip · falsy omit entirely. The three-way return is what lets an action explain why
  it is unavailable instead of silently vanishing.
- Context is `{ label, type, jiraKey, worktree }`; any field may be null, and each action
  declares what it needs. `trackedContext()` resolves the worktree via
  `isWorktreeActive`, so it is found by ticket key, not by path.
- `run(ctx)` may do anything — **these are not all program launchers.** Current actions
  download a file, open a URL, and start processes. Branch checkout / branch creation are
  the next ones and will open a dialog from `run`.
- The tracker bar renders `icon + label`; worktree rows render `icon` only, since row
  actions are hover-revealed and space is tight.
- `renderTracker()` runs every second and rebuilds these buttons each time, so actions
  must stay cheap and stateless.

Server side, `spawn.py` holds a **fixed table** of launchable tools. The client sends a
tool id and a path, never a command line, and `server.py` checks the path is one git
itself reports as a worktree (membership, not prefix — a prefix test would let any
subdirectory through). `POST /worktree/open` additionally requires a present, matching
`Origin` header, which is stricter than the read-only routes: it starts processes.

## Conventions
- XSS: always wrap user/external strings with `esc()` before innerHTML
- No comments in code unless the why is non-obvious
- No TypeScript, no build tooling
- `fmtDuration(ms)` → `"1h 05m"` / `"45m"` — use for all time display
- `todayStr()` → `"YYYY-MM-DD"` — use for date comparisons
- Context switch = `clockIn` called with a different label than `activeEntry.label`
- **Track labels are `"KEY: summary"`.** The Jira panel, the epic panel and the worktree
  panel all build labels this way on purpose, so time clocked on the same ticket from any
  of them aggregates into one row in the summary view. `jiraKeyFromLabel()` parses the key
  back out, which is what gates the tracker bar's "Save ticket" button — so that button
  works for worktree entries too. Do not invent a different label shape.
- The worktree panel's ▶ resolves its label through `jiraSummaries`, fetching a single
  issue summary on demand for branches whose ticket no panel loaded (someone else's
  ticket, or a closed one). No summary available falls back to the branch remainder.
- `clockIn`/`clockOut` call `renderWorktrees()` so the ▶/Tracking marker updates at once
  instead of waiting for the 10s poll.
- Entries older than 14 days are pruned on startup (`pruneOldEntries`)
- The 1-second interval calls both `renderTracker()` and `renderPomo()`
- `worktrees._cache` is a single immutable `(key, ts, data)` tuple swapped in one
  assignment. The server is threaded and the poller and dashboard both read it, so it must
  never be updated field by field.
- The worktree panel polls every 10s while the tab is visible. Its render order is fixed
  (repo name → main checkout first → worktree name) so a refresh never moves an item
  under the cursor.
- **Nothing repo-, product- or employer-specific in the code.** This repo is public.
  Repo paths, project keys and base branches are settings, never constants.
