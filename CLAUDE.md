# DevTodo — Claude context

## Run
`serve.bat` → `http://localhost:8080` (Python server in `server.py`)
Jira/GitLab calls are proxied through the server: `/proxy/jira/*`, `/proxy/gitlab/*`
Git worktree state comes from the server too: `/worktrees`, `/worktrees/scan`

## File layout
- `index.html` — all HTML + all JS in one IIFE (no build step, no modules)
- `styles.css` — all CSS, separate file
- `server.py` — local proxy server, routing only for anything non-trivial
- `fetch_mr_comments.py` — helper called by server.py
- `jira_downloader.py` — helper called by server.py
- `worktrees.py` — git worktree discovery (shells out to git, no repo-specific knowledge)
- `PLAN-worktrees.md` — phased plan for the worktree / auto-time-tracking work

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
| `Jira` | `jiraFetch`, `fetchMyJiraIssues`, `fetchSupportTickets`, `fetchEpicChildren`, `createEpicIssue`, `buildJiraItem`, `loadJira`, `loadEpicPanel` |
| `Jira sorting` | `priorityOrder`, `jiraStatusTier`, `issueInActiveSprint`, sort comparators |
| `GitLab` | `fetchGitLabMRs`, `buildGitLabItem`, `downloadMrComments`, `loadGitLab` |
| `MR ranking` | `mrRank` |
| `Local Todos` | `renderTodos`, `addTodo` |
| `Worktrees` | `ticketKeyOf`, `fmtAgo`, `buildWorktreeItem`, `loadWorktrees`, `scanForRepos`, `parseRoots` |
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
```

## Storage keys (all `devtodo_*`)
`settings`, `todos`, `show_unassigned`, `time_entries`, `time_active`, `time_suggestions`, `epic`, `pomo_state`, `pomo_cycles`, `ctx_switches`

## Conventions
- XSS: always wrap user/external strings with `esc()` before innerHTML
- No comments in code unless the why is non-obvious
- No TypeScript, no build tooling
- `fmtDuration(ms)` → `"1h 05m"` / `"45m"` — use for all time display
- `todayStr()` → `"YYYY-MM-DD"` — use for date comparisons
- Context switch = `clockIn` called with a different label than `activeEntry.label`
- Entries older than 14 days are pruned on startup (`pruneOldEntries`)
- The 1-second interval calls both `renderTracker()` and `renderPomo()`
- The worktree panel polls every 10s while the tab is visible. Its render order is fixed
  (repo name → main checkout first → worktree name) so a refresh never moves an item
  under the cursor.
- **Nothing repo-, product- or employer-specific in the code.** This repo is public.
  Repo paths, project keys and base branches are settings, never constants.
